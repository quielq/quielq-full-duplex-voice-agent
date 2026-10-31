"""WebRTC echo cancellation between the transport's output and input filters (ADR-013).

Pipecat has no AEC, so the agent's voice reaches the mic and it interrupts itself. The
output filter copies what is about to play; the input filter removes it from the mic.
Output comes in bursts, so reference audio waits in a queue and each 10 ms of mic audio
takes 10 ms of it. That keeps the reference ahead of the echo, as WebRTC needs.
"""

from __future__ import annotations

from collections import deque

import numpy as np
import soxr
from livekit import rtc

from pipecat.audio.filters.base_audio_filter import BaseAudioFilter
from pipecat.frames.frames import FilterControlFrame

MAX_REFERENCE_S = 1  # older reference can no longer match anything the mic hears
REPORT_FRAMES = 100  # one echo_window event per second, levels only, never audio


class EchoCanceller:
    def __init__(self, in_rate: int, out_rate: int, delay_ms: int, on_event=None):
        self._apm = rtc.AudioProcessingModule(echo_cancellation=True)
        self._rate = in_rate
        self._delay_ms = delay_ms
        self._samples = in_rate // 100  # WebRTC takes exactly 10 ms
        self._size = self._samples * 2
        self._resampler = (
            None if in_rate == out_rate else soxr.ResampleStream(out_rate, in_rate, 1, dtype="int16")
        )
        self._reference: deque[bytes] = deque(maxlen=MAX_REFERENCE_S * 100)
        self._reference_pending = bytearray()
        self._capture_pending = bytearray()
        self._on_event = on_event
        self._reset_window()

    def add_reference(self, audio: bytes) -> None:
        """Queue audio about to play, at the output rate."""
        if self._resampler:
            samples = np.frombuffer(audio, dtype=np.int16)
            audio = self._resampler.resample_chunk(samples).tobytes()
        self._reference_pending += audio
        while len(self._reference_pending) >= self._size:
            self._reference.append(bytes(self._reference_pending[: self._size]))
            del self._reference_pending[: self._size]

    def process(self, audio: bytes) -> bytes:
        """Return the mic audio with the echo removed, in whole 10 ms frames."""
        self._capture_pending += audio
        out = bytearray()
        while len(self._capture_pending) >= self._size:
            near = self._capture_pending[: self._size]
            del self._capture_pending[: self._size]
            far = self._next_reference()
            capture = rtc.AudioFrame(near, self._rate, 1, self._samples)
            self._apm.process_reverse_stream(rtc.AudioFrame(far, self._rate, 1, self._samples))
            self._apm.set_stream_delay_ms(self._delay_ms)
            self._apm.process_stream(capture)
            cancelled = bytes(capture.data)
            out += cancelled
            self._measure(bytes(near), far, cancelled)
        return bytes(out)

    def _reset_window(self) -> None:
        self._frames = 0
        self._underruns = 0
        self._max_queue = 0
        self._energy = {"near": 0.0, "far": 0.0, "out": 0.0}

    def _measure(self, near: bytes, far: bytes, out: bytes) -> None:
        if not self._on_event:
            return
        self._frames += 1
        for name, audio in (("near", near), ("far", far), ("out", out)):
            self._energy[name] += float(np.sum(np.frombuffer(audio, dtype=np.int16).astype(np.float64) ** 2))
        if self._frames == REPORT_FRAMES:
            samples = REPORT_FRAMES * self._samples
            levels = {
                f"{name}_db": round(10 * np.log10(max(energy / samples, 1e-3) / 32768**2), 1)
                for name, energy in self._energy.items()
            }
            self._on_event(
                "echo_window", underruns=self._underruns, max_queue=self._max_queue, **levels
            )
            self._reset_window()

    def _next_reference(self) -> bytes:
        self._max_queue = max(self._max_queue, len(self._reference))
        if self._reference:
            return self._reference.popleft()
        self._underruns += 1
        return bytes(self._size)

    def reference_filter(self, inner: BaseAudioFilter | None = None) -> BaseAudioFilter:
        """Output filter. ``inner`` runs first, so the reference includes any ducking."""
        return _ReferenceFilter(self, inner)

    def capture_filter(self) -> BaseAudioFilter:
        return _CaptureFilter(self)


class _ReferenceFilter(BaseAudioFilter):
    def __init__(self, canceller: EchoCanceller, inner: BaseAudioFilter | None):
        self._canceller = canceller
        self._inner = inner

    async def start(self, sample_rate: int):
        if self._inner:
            await self._inner.start(sample_rate)

    async def stop(self):
        if self._inner:
            await self._inner.stop()

    async def process_frame(self, frame: FilterControlFrame):
        if self._inner:
            await self._inner.process_frame(frame)

    async def filter(self, audio: bytes) -> bytes:
        if self._inner:
            audio = await self._inner.filter(audio)
        self._canceller.add_reference(audio)
        return audio


class _CaptureFilter(BaseAudioFilter):
    def __init__(self, canceller: EchoCanceller):
        self._canceller = canceller

    async def start(self, sample_rate: int):
        pass

    async def stop(self):
        pass

    async def process_frame(self, frame: FilterControlFrame):
        pass

    async def filter(self, audio: bytes) -> bytes:
        return self._canceller.process(audio)
