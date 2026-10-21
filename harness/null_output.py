"""A silent output transport paced like a sound card, with a capture of what "played".

Writes block for each frame's duration, as a device write would, so Pipecat's
interruption handling, bot-speaking frames, and word-timestamp text release behave
as they do with real playback. Every written frame is recorded with its monotonic
start time, which gives acoustic-style measurements without a speaker:

* ``first_audio_out``: first non-silent sample written after the bot starts a reply;
* ``audio_stopped``: last non-silent sample written before an interruption.
"""

from __future__ import annotations

import asyncio
import struct
import time
from dataclasses import dataclass

from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    OutputAudioRawFrame,
    StartFrame,
    TTSAudioRawFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import TransportParams

SILENCE_THRESHOLD = 64  # int16 peak below this counts as silence


def _peak(audio: bytes) -> int:
    n = len(audio) // 2
    if n == 0:
        return 0
    return max(abs(s) for s in struct.unpack(f"<{n}h", audio[: n * 2]))


@dataclass
class PlayedChunk:
    t_ns: int  # monotonic time the chunk started playing
    duration_s: float
    peak: int
    context_id: str | None


class PacedNullOutputTransport(BaseOutputTransport):
    def __init__(self, sample_rate: int, on_event=None, **kwargs):
        params = TransportParams(audio_out_enabled=True, audio_out_sample_rate=sample_rate)
        super().__init__(params, **kwargs)
        self._on_event = on_event or (lambda *a, **k: None)
        self._next_t = 0.0
        self.played: list[PlayedChunk] = []
        # Seconds of TTS audio received per context (generated), for heard-fraction checks.
        self.generated_s: dict[str, float] = {}
        self._last_voiced_ns: int | None = None
        self.interruptions_ns: list[int] = []
        self._voiced_since_interrupt = False

    async def start(self, frame: StartFrame):
        await super().start(frame)
        await self.set_transport_ready(frame)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        if isinstance(frame, TTSAudioRawFrame) and frame.context_id:
            dur = len(frame.audio) / 2 / frame.sample_rate
            self.generated_s[frame.context_id] = self.generated_s.get(frame.context_id, 0.0) + dur
        elif isinstance(frame, InterruptionFrame):
            self.interruptions_ns.append(time.monotonic_ns())
            self._on_event("interruption_at_output")
            if self._voiced_since_interrupt and self._last_voiced_ns is not None:
                self._on_event("audio_stopped_last_voiced", voiced_t_ns=self._last_voiced_ns)
            self._voiced_since_interrupt = False
        await super().process_frame(frame, direction)

    async def write_audio_frame(self, frame: OutputAudioRawFrame) -> bool:
        dur = len(frame.audio) / 2 / frame.sample_rate
        now = time.monotonic()
        if self._next_t < now:  # device underrun: playback restarts now
            self._next_t = now
        start = self._next_t
        self._next_t += dur
        start_ns = int(start * 1e9)
        peak = _peak(frame.audio)
        ctx = getattr(frame, "context_id", None)
        self.played.append(PlayedChunk(start_ns, dur, peak, ctx))
        if peak >= SILENCE_THRESHOLD:
            if not self._voiced_since_interrupt:
                self._on_event("first_audio_out_acoustic", play_t_ns=start_ns)
            self._voiced_since_interrupt = True
            self._last_voiced_ns = start_ns + int(dur * 1e9)
        # Block until this chunk has "played", like a device write with a short buffer.
        await asyncio.sleep(max(0.0, self._next_t - time.monotonic() - 0.02))
        return True

    def voiced_s_between(self, start_ns: int, end_ns: int) -> float:
        """Seconds of voiced audio that started playing in [start_ns, end_ns).

        The transport re-chunks audio and drops context ids, so replies are told apart by time.
        """
        return sum(
            c.duration_s for c in self.played
            if c.peak >= SILENCE_THRESHOLD and start_ns <= c.t_ns < end_ns
        )  # fmt: skip
