"""Where does speech fall behind? One audio_hop event per second, durations only, never audio.

Compares audio arriving from the TTS socket with audio written to the speakers, both in
milliseconds of speech. If arrival keeps up and writes stall, the output is holding it up.
If arrival itself falls behind, the delay is upstream of the transport.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from pipecat.frames.frames import TTSAudioRawFrame

MAX_SPEECH_GAP_S = 5  # longer is just the bot being quiet


class AudioHops:
    def __init__(self, on_event: Callable[..., object], *, window_s: float = 1.0, clock=time.monotonic):
        self._on_event = on_event
        self._window_s = window_s
        self._clock = clock
        self._window_start: float | None = None
        self._last_arrival: float | None = None
        self._last_activity: float | None = None
        self._backlog_ms = 0.0
        self._clear()

    def _clear(self) -> None:
        self._arrived_ms = 0.0
        self._written_ms = 0.0
        self._write_busy_ms = 0.0
        self._write_max_ms = 0.0
        self._writes = 0
        self._arrival_max_gap_ms = 0.0

    def on_arrival(self, nbytes: int, sample_rate: int) -> None:
        now = self._clock()
        self._close_if_idle(now)
        if self._last_arrival is not None and now - self._last_arrival < MAX_SPEECH_GAP_S:
            self._arrival_max_gap_ms = max(self._arrival_max_gap_ms, (now - self._last_arrival) * 1000)
        self._last_arrival = now
        speech_ms = nbytes / 2 / sample_rate * 1000
        self._arrived_ms += speech_ms
        self._backlog_ms += speech_ms
        self._tick(now)

    def on_write(self, nbytes: int, sample_rate: int, seconds: float) -> None:
        self._close_if_idle(self._clock())
        speech_ms = nbytes / 2 / sample_rate * 1000
        self._written_ms += speech_ms
        self._backlog_ms -= speech_ms
        self._writes += 1
        self._write_busy_ms += seconds * 1000
        self._write_max_ms = max(self._write_max_ms, seconds * 1000)
        self._tick(self._clock())

    def reset(self) -> None:
        """An interruption dropped whatever had not played."""
        self._backlog_ms = 0.0

    def _close_if_idle(self, now: float) -> None:
        """A wait between replies is not slow speech, so it never joins a window."""
        last = self._last_activity
        if self._window_start is None or last is None or now - last <= self._window_s:
            return
        if self._written_ms or self._arrived_ms:
            self._emit(last - self._window_start)
        self._window_start = None
        self._clear()

    def _tick(self, now: float) -> None:
        self._last_activity = now
        if self._window_start is None:
            self._window_start = now
        elapsed = now - self._window_start
        if elapsed < self._window_s:
            return
        self._emit(elapsed)
        self._window_start = now
        self._clear()

    def _emit(self, elapsed: float) -> None:
        self._on_event(
            "audio_hop",
            window_ms=round(elapsed * 1000),
            arrived_ms=round(self._arrived_ms),
            written_ms=round(self._written_ms),
            writes=self._writes,
            write_busy_ms=round(self._write_busy_ms),
            write_max_ms=round(self._write_max_ms),
            arrival_max_gap_ms=round(self._arrival_max_gap_ms),
            backlog_ms=round(self._backlog_ms),
        )


class HopTimedTTS:
    """Mixin for a TTS service: counts audio as it leaves the socket."""

    def __init__(self, *, hops: AudioHops | None = None, **kwargs):
        super().__init__(**kwargs)
        self._hops = hops

    async def append_to_audio_context(self, context_id, frame):
        if self._hops and isinstance(frame, TTSAudioRawFrame):
            self._hops.on_arrival(len(frame.audio), frame.sample_rate)
        await super().append_to_audio_context(context_id, frame)

    async def on_audio_context_interrupted(self, context_id: str):
        await super().on_audio_context_interrupted(context_id)
        if self._hops:
            self._hops.reset()


def trace_writes(output, hops: AudioHops) -> None:
    """Time each speaker write on the transport's output."""
    write = output.write_audio_frame

    async def timed_write(frame):
        start = time.monotonic()
        written = await write(frame)
        hops.on_write(len(frame.audio), frame.sample_rate, time.monotonic() - start)
        return written

    output.write_audio_frame = timed_write
