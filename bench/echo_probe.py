"""Can WebRTC echo cancellation stop Silero hearing the agent on laptop speakers?

Plays `say` speech through the speakers with nobody talking, records the mic, then runs
Silero with the profile's VAD settings on the raw mic and on the mic after WebRTC AEC.
One recording is replayed through a fresh canceller at each delay hint, so every setting
faces identical audio. No cloud calls, and audio stays in memory (never saved).

    uv run --with livekit python -m bench.echo_probe

PASS: Silero fires on the raw mic and not on the cancelled mic. livekit stays out of the
project dependencies unless this passes.
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

from harness.audio_io import resolve_device
from harness.config import load_config
from harness.fakes import STORY
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams, VADState

IN_RATE = 16000
OUT_RATE = 24000
FRAME = IN_RATE // 100  # WebRTC takes exactly 10 ms
LEAD_S = 0.5
TAIL_S = 1.0
DELAYS_MS = (0, 50, 100, 150, 200, 300)


def synthesize(text: str) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "probe.wav"
        subprocess.run(["say", "-o", str(path), f"--data-format=LEI16@{OUT_RATE}", text], check=True)
        with wave.open(str(path)) as w:
            return w.readframes(w.getnframes())


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
    from livekit import rtc

    apm = rtc.AudioProcessingModule(echo_cancellation=True)
    size = FRAME * 2
    out = []
    for start in range(0, len(near) - size + 1, size):
        reverse = rtc.AudioFrame(bytearray(far[start : start + size]), IN_RATE, 1, FRAME)
        capture = rtc.AudioFrame(bytearray(near[start : start + size]), IN_RATE, 1, FRAME)
        apm.process_reverse_stream(reverse)
        apm.set_stream_delay_ms(delay_ms)
        apm.process_stream(capture)
        out.append(bytes(capture.data))
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="mac")
    ap.add_argument("--input-device", default="MacBook Air Microphone")
    ap.add_argument("--output-device", default="MacBook Air Speakers")
    args = ap.parse_args()
    cfg = load_config(args.profile)
    far = synthesize(STORY)
    near = play_and_record(
        far, resolve_device(args.input_device, "input"), resolve_device(args.output_device, "output")
    )
    report = asyncio.run(evaluate(near, reference(far, len(near)), cfg.profile["vad"]))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
