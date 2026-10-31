"""Waiting phrase (ADR-009): a short local clip while the agent is working.

Sits between the TTS and the output. It plays when a tool call starts, or when no reply audio
has started ``after_ms`` after end of turn. At most once per turn, never once the reply has
started, and queued clip audio is flushed by any interruption like other output audio.

It is raw output audio with no text, so it never enters the LLM context or the
``played_up_to`` word count. The timer needs ``is_active``, so that with ACTIVE/IDLE built it
cannot say "one sec" to a side conversation. ``never_tools`` keeps ``stay_silent`` quiet.

It must not count toward latency, and it hides the reply's own start: one BotStartedSpeaking
covers a clip and a reply that follows straight on. So a clip claims the next bot start, however
late it comes, and the observer logs that one as ``filler_audio_out``. This processor logs
``first_audio_out`` for the reply when the clip ends, marked ``estimated`` because that is the
queue's arithmetic and not a measurement.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable, Sequence

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    FunctionCallInProgressFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class FillerProcessor(FrameProcessor):
    def __init__(
        self,
        clip: bytes,
        sample_rate: int,
        *,
        after_ms: float = 600,
        is_active: Callable[[], bool] = lambda: True,
        never_tools: Sequence[str] = ("stay_silent",),
        on_event: Callable[..., object] | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._clip = clip
        self._sample_rate = sample_rate
        self._after_s = after_ms / 1000
        self._is_active = is_active
        self._never_tools = set(never_tools)
        self._on_event = on_event or (lambda *a, **k: None)
        self._played = False
        self._reply_started = False
        self._end_ns = 0
        self._bot_stopped_since_clip = False
        self._bot_speaking = False
        self._awaiting_bot_start = False
        self._clip_started = asyncio.Event()
        self._timer: asyncio.Task | None = None

    @property
    def _clip_ns(self) -> int:
        return len(self._clip) * 500_000_000 // self._sample_rate

    def claim_bot_start(self) -> bool:
        """True once, for the bot start this clip caused. Called by the event observer."""
        if not self._awaiting_bot_start:
            return False
        self._awaiting_bot_start = False
        self._end_ns = time.monotonic_ns() + self._clip_ns
        self._clip_started.set()
        return True

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, UserStartedSpeakingFrame):
            self._played = self._reply_started = self._awaiting_bot_start = False
            await self._cancel_timer()
        elif isinstance(frame, UserStoppedSpeakingFrame):
            if self._is_active():
                await self._cancel_timer()
                self._timer = self.create_task(self._after_silence())
        elif isinstance(frame, InterruptionFrame):
            await self._cancel_timer()
            self._end_ns = 0
            self._awaiting_bot_start = False
        elif isinstance(frame, FunctionCallInProgressFrame):
            if frame.function_name not in self._never_tools:
                await self._cancel_timer()
                await self._trigger("tool_call")
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            self._bot_stopped_since_clip = True
        elif isinstance(frame, (TTSStartedFrame, TTSAudioRawFrame)):
            await self._reply_begins()
        await self.push_frame(frame, direction)

    async def _after_silence(self) -> None:
        await asyncio.sleep(self._after_s)
        await self._trigger("timer")

    async def _trigger(self, reason: str) -> None:
        if self._played or self._reply_started:
            return
        self._played = True
        self._bot_stopped_since_clip = False
        self._clip_started.clear()
        # With the bot already speaking there is no new start to claim.
        self._awaiting_bot_start = not self._bot_speaking
        if self._bot_speaking:
            self._clip_started.set()
        self._end_ns = time.monotonic_ns() + self._clip_ns
        self._on_event("filler_trigger", reason=reason)
        chunk = self._sample_rate // 50 * 2  # 20 ms of 16-bit samples
        for i in range(0, len(self._clip), chunk):
            await self.push_frame(
                OutputAudioRawFrame(self._clip[i : i + chunk], self._sample_rate, 1)
            )

    async def _reply_begins(self) -> None:
        if self._reply_started:
            return
        self._reply_started = True
        await self._cancel_timer()
        if self._played and not self._bot_stopped_since_clip:
            self.create_task(self._log_reply_audio())

    async def _log_reply_audio(self) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._clip_started.wait(), 2.0)
        await asyncio.sleep(max(0, self._end_ns - time.monotonic_ns()) / 1e9)
        if self._end_ns:  # zero means an interruption cut this reply off
            self._on_event("first_audio_out", estimated=True)

    async def _cancel_timer(self) -> None:
        # Never cancel the task we are running in (the timer itself may call this).
        timer, self._timer = self._timer, None
        if timer and not timer.done() and timer is not asyncio.current_task():
            await self.cancel_task(timer)

    async def cleanup(self):
        await super().cleanup()
        await self._cancel_timer()
