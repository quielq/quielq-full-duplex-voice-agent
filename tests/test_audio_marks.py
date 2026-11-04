"""Sample-level audio marks for the latency benchmark (ADR-036). No audio devices, no spend."""

import asyncio

import numpy as np
from pipecat.frames.frames import OutputAudioRawFrame, TTSAudioRawFrame

from harness.audio_marks import InputVoiceTap, OutputVoiceTap, stamp_capture
from harness.filler import FillerProcessor

SR = 16000


def pcm(*parts: tuple[float, int]) -> bytes:
    """Concatenate (seconds, amplitude) runs of a constant-magnitude square wave."""
    out = []
    for seconds, amp in parts:
        n = int(seconds * SR)
        out.append((np.where(np.arange(n) % 2, amp, -amp)).astype("<i2"))
    return np.concatenate(out).tobytes()


def feed(tap: InputVoiceTap, audio: bytes, t0_ns: int = 0) -> None:
    chunk = SR // 50 * 2
    for i in range(0, len(audio), chunk):
        tap.observe(audio[i : i + chunk], SR, t0_ns + int(i / 2 / SR * 1e9))


def test_input_marks_the_last_voiced_sample_not_the_frame():
    events = []
    tap = InputVoiceTap(lambda name, **f: events.append((name, f)))
    # 0.5 s silence, 0.2 s speech, a 0.1 s pause that is not an end, 0.3 s speech, then quiet.
    feed(tap, pcm((0.5, 0), (0.2, 3000), (0.1, 0), (0.305, 3000), (0.5, 100)))
    names = [n for n, _ in events]
    assert names == ["user_audio_onset", "user_audio_end"]
    assert events[0][1]["audio_t_ns"] == 500_000_000
    end_ms = events[1][1]["audio_t_ns"] / 1e6
    assert abs(end_ms - 1105) < 0.2  # inside a 20 ms frame, to the sample
    assert events[1][1]["speech_ms"] == 605


def test_adaptive_threshold_rises_over_a_noisy_floor():
    events = []
    tap = InputVoiceTap(lambda name, **f: events.append(name), adaptive=True)
    feed(tap, pcm((2.0, 400)))  # room noise under the 500 floor
    assert tap.threshold == 1600
    feed(tap, pcm((0.3, 800)), 2_000_000_000)  # louder noise, still under four times the floor
    assert events == []


def out_frame(cls, seconds, amp, sr=24000):
    n = int(seconds * sr)
    audio = np.full(n, amp, dtype="<i2").tobytes()
    return cls(audio, sample_rate=sr, num_channels=1)


def test_output_tells_the_thinking_phrase_from_the_reply():
    events = []
    clock = iter(i * 0.001 for i in range(10_000))
    tap = OutputVoiceTap(lambda name, **f: events.append((name, f)), device_latency_s=0.0,
                         clock=lambda: next(clock))  # fmt: skip
    for _ in range(5):
        tap.observe(out_frame(OutputAudioRawFrame, 0.04, 5000))  # the clip
    tap.observe(out_frame(TTSAudioRawFrame, 0.04, 0))  # leading silence in the reply
    for _ in range(3):
        tap.observe(out_frame(TTSAudioRawFrame, 0.04, 5000))
    names = [n for n, _ in events]
    assert names == ["local_audio_onset", "reply_audio_onset"]
    # Play time is the queue: five clip chunks and one silent chunk before the reply sample.
    assert events[1][1]["audio_t_ns"] == 240_000_000
    assert events[1][1]["boundary"] is False


def test_output_onset_again_after_a_gap_and_adds_device_latency():
    events = []
    now = [0.0]
    tap = OutputVoiceTap(lambda name, **f: events.append(f["audio_t_ns"]), device_latency_s=0.05,
                         clock=lambda: now[0])  # fmt: skip
    tap.observe(out_frame(TTSAudioRawFrame, 0.04, 5000))
    now[0] = 2.0
    tap.observe(out_frame(TTSAudioRawFrame, 0.04, 5000))
    assert events == [50_000_000, 2_050_000_000]


async def test_attach_wraps_the_write():
    class Output:
        written = 0

        async def write_audio_frame(self, frame):
            self.written += 1
            return True

    events = []
    out = Output()
    OutputVoiceTap(lambda name, **f: events.append(name)).attach(out)
    assert await out.write_audio_frame(out_frame(TTSAudioRawFrame, 0.02, 5000))
    assert out.written == 1 and events == ["audio_out_latency", "reply_audio_onset"]


async def test_stamp_capture_pairs_callbacks_with_pushes():
    class Input:
        def __init__(self):
            self.pushed = []
            self.loop = asyncio.get_running_loop()

        def _audio_in_callback(self, in_data, frame_count, time_info, status):
            frame = OutputAudioRawFrame(in_data, sample_rate=SR, num_channels=1)
            asyncio.run_coroutine_threadsafe(self.push_audio_frame(frame), self.loop)

        async def push_audio_frame(self, frame):
            self.pushed.append(frame)

    inp = Input()
    stamp_capture(inp)
    await asyncio.to_thread(inp._audio_in_callback, bytes(640), 320, {}, 0)
    await asyncio.sleep(0.05)
    (frame,) = inp.pushed
    assert frame.capture_ns > 0


def test_filler_clips_are_whole_output_chunks():
    proc = FillerProcessor([b"\1\0" * 1000, b"\1\0" * 960], 24000)
    assert [len(c) for c in proc._clips] == [1920 * 2, 960 * 2]
