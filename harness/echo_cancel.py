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


class EchoCanceller:
    def __init__(self, in_rate: int, out_rate: int, delay_ms: int):
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
            far = self._reference.popleft() if self._reference else bytes(self._size)
            capture = rtc.AudioFrame(near, self._rate, 1, self._samples)
            self._apm.process_reverse_stream(rtc.AudioFrame(far, self._rate, 1, self._samples))
            self._apm.set_stream_delay_ms(self._delay_ms)
            self._apm.process_stream(capture)
            out += bytes(capture.data)
        return bytes(out)

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
