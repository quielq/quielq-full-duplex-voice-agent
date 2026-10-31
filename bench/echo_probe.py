"""Can WebRTC echo cancellation stop Silero hearing the agent on laptop speakers?

Plays `say` speech through the speakers with nobody talking, records the mic, then runs
Silero with the profile's VAD settings on the raw mic and on the mic after WebRTC AEC.
One recording is replayed through a fresh canceller at each delay hint, so every setting
faces identical audio. No cloud calls, and audio stays in memory (never saved).

    uv run python -m bench.echo_probe            # bot voice only
    uv run python -m bench.echo_probe --talker   # plus a second voice over it
    uv run python -m bench.echo_probe --transport   # through the real Pipecat transport

PASS: Silero fires on the raw mic and not on the cancelled mic. With --talker it must
also fire on each talker and nowhere else. The talker comes from the same speakers, so
it stands in for a person, and the real barge-in test is live.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path

import numpy as np
import soxr

from harness.audio_io import local_transport, resolve_device
from harness.config import load_config
from harness.echo_cancel import EchoCanceller
from harness.fakes import STORY
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams, VADState
from pipecat.frames.frames import TTSAudioRawFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.workers.runner import WorkerRunner

IN_RATE = 16000
OUT_RATE = 24000
FRAME = IN_RATE // 100  # WebRTC takes exactly 10 ms
LEAD_S = 0.5
TAIL_S = 1.0
DELAYS_MS = (0, 50, 100, 150, 200, 300)
BARGE_TEXT = "Wait, stop, cancel that please."
BARGE_VOICE = "Daniel"
BARGE_AT_S = (8.0, 16.0)
GRACE_S = 0.5  # an onset can land just after the talker stops


def synthesize(text: str, *voice: str) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "probe.wav"
        args = ["say", *voice, "-o", str(path), f"--data-format=LEI16@{OUT_RATE}", text]
        subprocess.run(args, check=True)
        with wave.open(str(path)) as w:
            return w.readframes(w.getnframes())


def mix_talker(far: bytes, talker: bytes) -> tuple[bytes, list[tuple[float, float]]]:
    """Add ``talker`` to the speaker audio only, at ``BARGE_AT_S``. Returns windows from capture start.

    It leaves the speakers but is missing from the reference, so to the canceller it is
    someone talking over the bot.
    """
    mixed = np.frombuffer(far, dtype=np.int16).astype(np.int32)
    voice = np.frombuffer(talker, dtype=np.int16)
    windows = []
    for at_s in BARGE_AT_S:
        start = int(at_s * OUT_RATE)
        mixed[start : start + len(voice)] += voice[: len(mixed) - start]
        windows.append((LEAD_S + at_s, LEAD_S + at_s + len(voice) / OUT_RATE))
    return np.clip(mixed, -32768, 32767).astype(np.int16).tobytes(), windows


def play_and_record(far: bytes, input_index: int | None, output_index: int | None) -> bytes:
    """Play ``far`` (24 kHz) after ``LEAD_S`` of capture; return the mic at 16 kHz."""
    import pyaudio

    pa = pyaudio.PyAudio()
    mic = pa.open(rate=IN_RATE, channels=1, format=pyaudio.paInt16, input=True,
                  input_device_index=input_index, frames_per_buffer=FRAME)  # fmt: skip
    speaker = pa.open(rate=OUT_RATE, channels=1, format=pyaudio.paInt16, output=True,
                      output_device_index=output_index)  # fmt: skip

    def play():
        time.sleep(LEAD_S)
        speaker.write(far)

    player = threading.Thread(target=play)
    total_s = LEAD_S + len(far) / 2 / OUT_RATE + TAIL_S
    chunks = []
    try:
        player.start()
        for _ in range(int(total_s * IN_RATE / FRAME)):
            chunks.append(mic.read(FRAME, exception_on_overflow=False))
    finally:
        player.join()
        speaker.close()
        mic.close()
        pa.terminate()
    return b"".join(chunks)


def reference(far: bytes, length: int) -> bytes:
    """``far`` at the mic's rate, padded so index 0 is the start of capture."""
    samples = soxr.resample(np.frombuffer(far, dtype=np.int16), OUT_RATE, IN_RATE)
    pad = np.zeros(int(LEAD_S * IN_RATE), dtype=np.int16)
    padded = np.concatenate([pad, samples.astype(np.int16)])[: length // 2]
    return np.pad(padded, (0, length // 2 - len(padded))).astype(np.int16).tobytes()


def measure_lag_ms(far: bytes, near: bytes) -> int:
    """Delay of the speaker's sound in the mic, by cross-correlation."""
    a = np.frombuffer(far, dtype=np.int16).astype(np.float32)
    b = np.frombuffer(near, dtype=np.int16).astype(np.float32)
    n = len(a) + len(b)
    corr = np.fft.irfft(np.fft.rfft(b, n) * np.conj(np.fft.rfft(a, n)), n)
    return round(int(np.argmax(np.abs(corr[: IN_RATE]))) * 1000 / IN_RATE)


def cancel(near: bytes, far: bytes, delay_ms: int) -> bytes:
    """Through the live EchoCanceller, in the transport's chunk sizes."""
    canceller = EchoCanceller(IN_RATE, IN_RATE, delay_ms)
    reference_chunk, capture_chunk = FRAME * 8, FRAME * 4  # 40 ms out, 20 ms in
    out = []
    for start in range(0, len(near), capture_chunk):
        if start % reference_chunk == 0:
            canceller.add_reference(far[start : start + reference_chunk])
        out.append(canceller.process(near[start : start + capture_chunk]))
    return b"".join(out)


def rms_db(audio: bytes) -> float:
    samples = np.frombuffer(audio, dtype=np.int16).astype(np.float64)
    return round(20 * np.log10(max(np.sqrt(np.mean(samples**2)), 1e-3) / 32768), 1)


async def speech_onsets(audio: bytes, vad_cfg: dict) -> tuple[list[float], float]:
    """Times Silero declares speech, and total seconds it stays in speech."""
    params = VADParams(**{k: vad_cfg[k] for k in ("confidence", "start_secs", "stop_secs", "min_volume")})
    vad = SileroVADAnalyzer(params=params)
    vad.set_sample_rate(IN_RATE)
    size = vad.num_frames_required() * 2
    onsets, windows, previous = [], 0, VADState.QUIET
    for start in range(0, len(audio) - size + 1, size):
        state = await vad.analyze_audio(audio[start : start + size])
        if state == VADState.SPEAKING:
            windows += 1
            if previous != VADState.SPEAKING:
                onsets.append(round(start / 2 / IN_RATE, 2))
        previous = state
    return onsets, round(windows * size / 2 / IN_RATE, 2)


async def evaluate(near: bytes, far: bytes, vad_cfg: dict) -> dict:
    lag_ms = measure_lag_ms(far, near)
    raw_onsets, raw_speech = await speech_onsets(near, vad_cfg)
    report = {
        "mic_db": rms_db(near),
        "lag_ms": lag_ms,
        "raw_onsets": len(raw_onsets),
        "raw_speech_s": raw_speech,
        "delays": [],
    }
    for delay in sorted({*DELAYS_MS, lag_ms}):
        cancelled = cancel(near, far, delay)
        onsets, speech = await speech_onsets(cancelled, vad_cfg)
        report["delays"].append(
            {
                "delay_ms": delay,
                "cancelled_db": rms_db(cancelled),
                "onsets": len(onsets),
                "speech_s": speech,
                "first_onset_s": onsets[0] if onsets else None,
                "last_onset_s": onsets[-1] if onsets else None,
            }
        )
    best = min(report["delays"], key=lambda d: (d["onsets"], d["speech_s"]))
    report["best"] = best
    if not report["raw_onsets"]:
        report["verdict"] = "INCONCLUSIVE: Silero never fired on the raw mic"
    else:
        report["verdict"] = "PASS" if best["onsets"] == 0 else "FAIL"
    return report


async def evaluate_talker(
    near: bytes, far: bytes, vad_cfg: dict, windows: list[tuple[float, float]], delay_ms: int
) -> dict:
    """Does the talker survive cancellation while the bot's voice stays out?"""
    onsets, speech = await speech_onsets(cancel(near, far, delay_ms), vad_cfg)
    heard = [
        next((round(o - start, 2) for o in onsets if start <= o <= end + GRACE_S), None)
        for start, end in windows
    ]
    in_window = lambda o: any(start <= o <= end + GRACE_S for start, end in windows)  # noqa: E731
    false_onsets = [o for o in onsets if not in_window(o)]
    return {
        "delay_ms": delay_ms,
        "talkers": len(windows),
        "onset_after_talker_start_s": heard,
        "false_onsets": false_onsets,
        "speech_s": speech,
        "verdict": "PASS" if all(h is not None for h in heard) and not false_onsets else "FAIL",
    }


class _WriteProxy:
    def __init__(self, stream, write):
        self._stream = stream
        self.write = write

    def __getattr__(self, name):
        return getattr(self._stream, name)


class RecordingCanceller(EchoCanceller):
    """Keeps what the live path saw, so the same audio can be replayed offline."""

    def __init__(self, *args):
        super().__init__(*args)
        self.raw = bytearray()
        self.cancelled = bytearray()
        self.used_reference = bytearray()
        self.underruns = 0
        self.max_queue = 0

    def _next_reference(self) -> bytes:
        self.max_queue = max(self.max_queue, len(self._reference))
        self.underruns += not self._reference
        frame = super()._next_reference()
        self.used_reference += frame
        return frame

    def process(self, audio: bytes) -> bytes:
        self.raw += audio
        out = super().process(audio)
        self.cancelled += out
        return out


async def play_through_transport(far: bytes, audio_cfg: dict, seconds: float, canceller, vad_cfg) -> dict:
    """Real transport and Silero as in the bot, with audio arriving in pieces like Deepgram's."""
    transport = local_transport(audio_cfg, canceller=canceller)
    params = VADParams(**{k: vad_cfg[k] for k in ("confidence", "start_secs", "stop_secs", "min_volume")})
    vad = VADProcessor(vad_analyzer=SileroVADAnalyzer(params=params))
    output = transport.output()
    pipeline = Pipeline([transport.input(), vad, output])
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(audio_in_sample_rate=IN_RATE, audio_out_sample_rate=OUT_RATE),
        idle_timeout_secs=None,
    )
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    writes: list[tuple[float, float]] = []

    async def script():
        await asyncio.sleep(1.0)
        stream, original = output._out_stream, output._out_stream.write

        def timed_write(audio):
            start = time.monotonic()
            original(audio)
            writes.append((start, time.monotonic() - start))

        stream_proxy = _WriteProxy(stream, timed_write)
        output._out_stream = stream_proxy
        piece = OUT_RATE * 2 // 10
        for start in range(0, len(far), piece):
            await worker.queue_frame(TTSAudioRawFrame(far[start : start + piece], OUT_RATE, 1))
            await asyncio.sleep(0.05)
        while writes and time.monotonic() - writes[-1][0] < 1.0:
            await asyncio.sleep(0.1)
        await asyncio.sleep(TAIL_S)
        await worker.cancel()

    await asyncio.gather(runner.run(), script())
    durations = np.array([d for _, d in writes]) * 1000
    wall = writes[-1][0] + writes[-1][1] - writes[0][0]
    return {
        "audio_s": round(seconds, 2),
        "playback_wall_s": round(wall, 2),
        "stretch": round(wall / seconds, 2),
        "write_ms_median": round(float(np.median(durations)), 1),
        "write_ms_max": round(float(durations.max()), 1),
        "writes_over_100ms": int((durations > 100).sum()),
    }


async def evaluate_transport(far: bytes, cfg, delay_ms: int, in_name: str, out_name: str) -> dict:
    audio_cfg = {**cfg.profile["audio"], "echo_cancel": True, "echo_delay_ms": delay_ms,
                 "input_device": in_name, "output_device": out_name}  # fmt: skip
    canceller = RecordingCanceller(IN_RATE, OUT_RATE, delay_ms)
    vad_cfg = cfg.profile["vad"]
    output_stats = await play_through_transport(
        far, audio_cfg, len(far) / 2 / OUT_RATE, canceller, vad_cfg
    )
    raw, live = bytes(canceller.raw), bytes(canceller.cancelled)
    used = bytes(canceller.used_reference)
    raw_onsets, raw_speech = await speech_onsets(raw, vad_cfg)
    live_onsets, live_speech = await speech_onsets(live, vad_cfg)
    replay = cancel(raw, used, delay_ms)
    replay_onsets, replay_speech = await speech_onsets(replay, vad_cfg)
    step = IN_RATE * 2 * 2  # bytes in 2 s
    timeline = [
        {"t_s": i // (IN_RATE * 2), "reference": rms_db(used[i : i + step]),
         "raw": rms_db(raw[i : i + step]), "live": rms_db(live[i : i + step])}
        for i in range(0, min(len(used), len(raw), len(live)) - step + 1, step)
    ]  # fmt: skip
    return {
        "output": output_stats,
        "timeline": timeline,
        "delay_ms": delay_ms,
        "mic_db": rms_db(raw),
        "reference_lag_ms": measure_lag_ms(used, raw),
        "underruns": canceller.underruns,
        "max_queue_frames": canceller.max_queue,
        "raw": {"onsets": raw_onsets, "speech_s": raw_speech},
        "live": {"db": rms_db(live), "onsets": live_onsets, "speech_s": live_speech},
        "replay_same_reference": {
            "db": rms_db(replay), "onsets": replay_onsets, "speech_s": replay_speech,
        },
    }  # fmt: skip


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="mac")
    ap.add_argument("--input-device", default="MacBook Air Microphone")
    ap.add_argument("--output-device", default="MacBook Air Speakers")
    ap.add_argument("--talker", action="store_true", help="add a voice the canceller must keep")
    ap.add_argument("--delay-ms", type=int, default=50, help="delay hint for --talker")
    ap.add_argument("--transport", action="store_true", help="through the real Pipecat transport")
    args = ap.parse_args()
    cfg = load_config(args.profile)
    vad_cfg = cfg.profile["vad"]
    far = synthesize(STORY)
    if args.transport:
        report = asyncio.run(
            evaluate_transport(far, cfg, args.delay_ms, args.input_device, args.output_device)
        )
        print(json.dumps(report, indent=2))
        return
    played, windows = mix_talker(far, synthesize(BARGE_TEXT, "-v", BARGE_VOICE)) if args.talker else (far, [])
    near = play_and_record(
        played, resolve_device(args.input_device, "input"), resolve_device(args.output_device, "output")
    )
    ref = reference(far, len(near))
    if args.talker:
        report = asyncio.run(evaluate_talker(near, ref, vad_cfg, windows, args.delay_ms))
    else:
        report = asyncio.run(evaluate(near, ref, vad_cfg))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
