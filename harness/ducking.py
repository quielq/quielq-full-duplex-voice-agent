"""Duck-then-decide barge-in (step 5 prototype).

Pipecat has no duck mode: by default any user speech during playback interrupts at
once. Here, user-turn interruptions are switched off and two pieces share one state:

* ``DuckGain`` is the output transport's ``audio_out_filter``. Pipecat applies it as each
  frame leaves the output queue, so a gain change takes effect on the next write even
  when seconds of TTS audio are already queued. (A processor placed before the output,
  as the review sketched, would only reach audio queued after the duck.)
* ``DuckingProcessor`` sits after the STT, where it sees VAD frames, transcripts, and the
  bot's speaking state. On speech onset during playback it ducks to ``gain`` and then:

  - a keyword on the allowlist (stop, wait, no, cancel, pause) interrupts at once;
  - speech still going after ``backchannel_max_s``, or with more than
    ``backchannel_max_words`` words, interrupts;
  - otherwise (a short backchannel such as "mm-hmm") it restores the volume and drops
    the transcript, so the user turn ends empty and the bot keeps talking.

  Interrupting uses ``broadcast_interruption``, which cancels the LLM and TTS and flushes
  queued output audio.

Not built yet (step 5 scope): no-wake-word addressing, resume policies, echo handling.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable, Sequence

from pipecat.audio.filters.base_audio_filter import BaseAudioFilter
from pipecat.audio.utils import scale_audio
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    FilterControlFrame,
    Frame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

DEFAULT_ALLOWLIST = ("stop", "wait", "no", "cancel", "pause")


class DuckGain(BaseAudioFilter):
    """Output filter with a gain the DuckingProcessor sets directly."""

    def __init__(self, on_event: Callable[..., object] | None = None):
        self.gain = 1.0
        self._on_event = on_event or (lambda *a, **k: None)
        self._applied = False

    def set_gain(self, gain: float) -> None:
        self.gain = gain
        self._applied = False

    async def start(self, sample_rate: int):
        pass

    async def stop(self):
        pass

    async def process_frame(self, frame: FilterControlFrame):
        pass

    async def filter(self, audio: bytes) -> bytes:
        if not self._applied:
            # First write at the new gain: the moment the volume actually changes.
            self._applied = True
            self._on_event("gain_applied", gain=self.gain, write_t_ns=time.monotonic_ns())
        return audio if self.gain == 1.0 else scale_audio(audio, self.gain)


class DuckingProcessor(FrameProcessor):
    def __init__(
        self,
        duck: DuckGain,
        *,
        gain: float = 0.3,
        backchannel_max_s: float = 1.0,
        backchannel_max_words: int = 1,
        allowlist: Sequence[str] = DEFAULT_ALLOWLIST,
        on_event: Callable[..., object] | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._duck = duck
        self._gain = gain
        self._max_s = backchannel_max_s
        self._max_words = backchannel_max_words
        if not all(isinstance(w, str) for w in allowlist):
            raise TypeError(f"allowlist must be strings (quote 'no' in YAML): {allowlist!r}")
        self._allow = re.compile(r"\b(" + "|".join(map(re.escape, allowlist)) + r")\b", re.I)
        self._on_event = on_event or (lambda *a, **k: None)
        self._bot_speaking = False
        self._ducked = False
        self._onset_t = 0.0
        self._timer: asyncio.Task | None = None
        self._commit_wait_s = 0.1

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
        elif isinstance(frame, InterruptionFrame):
            await self._reset()
        elif isinstance(frame, VADUserStartedSpeakingFrame) and self._bot_speaking:
            await self._start_duck()
        elif isinstance(frame, (InterimTranscriptionFrame, TranscriptionFrame)) and self._ducked:
            reason = (
                "allowlist" if self._allow.search(frame.text)
                else "words" if len(frame.text.split()) > self._max_words
                else None
            )  # fmt: skip
            if reason:
                await self._interrupt(reason, text=frame.text)
                if isinstance(frame, TranscriptionFrame):
                    # Let the interruption commit the assistant's partial reply before this
                    # user message reaches the context, so the history stays in order.
                    await asyncio.sleep(self._commit_wait_s)
            elif isinstance(frame, TranscriptionFrame):
                # A short final transcript with no command word: a backchannel. Restore the
                # volume and drop it, so the user turn ends empty and the bot keeps talking.
                await self._restore("backchannel", text=frame.text)
                return
        elif isinstance(frame, VADUserStoppedSpeakingFrame) and self._ducked:
            # Fallback if no transcript ever arrives (noise, a cough the STT ignores).
            await self._cancel_timer()
            self._timer = self.create_task(self._restore_if_no_transcript())

        await self.push_frame(frame, direction)

    async def _start_duck(self) -> None:
        if self._ducked:
            return
        self._ducked = True
        self._onset_t = time.monotonic()
        self._duck.set_gain(self._gain)
        self._on_event("duck")
        await self._cancel_timer()
        self._timer = self.create_task(self._interrupt_if_still_talking())

    async def _interrupt_if_still_talking(self) -> None:
        await asyncio.sleep(self._max_s)
        if self._ducked:
            await self._interrupt("duration")

    async def _restore_if_no_transcript(self) -> None:
        await asyncio.sleep(self._max_s)
        if self._ducked:
            await self._restore("no_transcript")

    async def _interrupt(self, reason: str, text: str = "") -> None:
        if not self._ducked:
            return
        self._on_event("barge_in_interrupt", reason=reason, text=text,
                       decide_ms=round((time.monotonic() - self._onset_t) * 1000))  # fmt: skip
        await self._reset()
        await self.broadcast_interruption()

    async def _restore(self, reason: str, text: str = "") -> None:
        self._on_event("unduck", reason=reason, text=text,
                       decide_ms=round((time.monotonic() - self._onset_t) * 1000))  # fmt: skip
        await self._reset()

    async def _reset(self) -> None:
        self._ducked = False
        self._duck.set_gain(1.0)
        await self._cancel_timer()

    async def _cancel_timer(self) -> None:
        # Never cancel the task we are running in (the timer itself may call this).
        timer, self._timer = self._timer, None
        if timer and not timer.done() and timer is not asyncio.current_task():
            await self.cancel_task(timer)

    async def cleanup(self):
        await super().cleanup()
        await self._cancel_timer()
