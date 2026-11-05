"""Do not answer the bot's own words heard back through the speakers (ADR-042).

On speakers, WebRTC AEC leaves some echo. On Nov 4 (`20261104-202417-64248a`) enough of
"I can't delete" got through that Flux started a user turn, the bot cut itself off, and then it
answered its own words. This guard cannot stop the cut-off, since Flux starts the turn before any
transcript exists, but it stops the answer.

``SpokenTap`` sits after the TTS and records the words the bot sends to the speaker.
``SelfEchoFilter`` sits before the user aggregator and drops a final transcript when all of:

- the turn began while the bot was speaking, so it cut the bot off;
- it has at least ``min_words`` words, and they appear in the same order, with nothing between
  them, among the words the bot spoke in the last ``window_s``;
- it has no allowlist word (stop, wait, no, cancel, pause), which always counts as the user.

The turn then ends with no transcript, so Pipecat's empty turn recovery runs as for any
unrecognised sound that cut the bot off: one LLM call that asks for a repeat and repeats a
question the user may have missed. With addressing on, that recovery is off and the bot stays
quiet. A dropped transcript logs ``self_echo``.

It is on only with ``audio.echo_cancel``: with headphones there is no echo to catch, and a person
who quotes the bot must never be ignored for nothing.
"""

from __future__ import annotations

import re
import time
from collections import deque
from collections.abc import Callable, Sequence

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    TranscriptionFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

WORD = re.compile(r"[a-z0-9']+")


def words(text: str) -> list[str]:
    return WORD.findall(text.lower().replace("’", "'"))


class SpokenWords:
    """The bot's recent words, with the time each was sent to the speaker."""

    def __init__(self, window_s: float = 15.0, clock: Callable[[], float] = time.monotonic):
        self._window_s = window_s
        self._clock = clock
        self._words: deque[tuple[float, str]] = deque(maxlen=400)

    def add(self, text: str) -> None:
        now = self._clock()
        self._words.extend((now, w) for w in words(text))

    def recent(self) -> list[str]:
        cutoff = self._clock() - self._window_s
        return [w for t, w in self._words if t >= cutoff]


def is_run_of(heard: Sequence[str], spoken: Sequence[str]) -> bool:
    n = len(heard)
    return n > 0 and any(list(spoken[i : i + n]) == list(heard) for i in range(len(spoken) - n + 1))


class SpokenTap(FrameProcessor):
    """Records the text the TTS sends on, and passes every frame unchanged."""

    def __init__(self, spoken: SpokenWords, **kwargs):
        super().__init__(**kwargs)
        self._spoken = spoken

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSTextFrame) and direction == FrameDirection.DOWNSTREAM:
            self._spoken.add(frame.text)
        await self.push_frame(frame, direction)


class SelfEchoFilter(FrameProcessor):
    def __init__(
        self,
        spoken: SpokenWords,
        *,
        min_words: int = 3,
        allowlist: Sequence[str] = ("stop", "wait", "no", "cancel", "pause"),
        on_event: Callable[..., object] | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._spoken = spoken
        self._min_words = min_words
        self._allow = {w.lower() for w in allowlist}
        self._on_event = on_event or (lambda *a, **k: None)
        self._bot_speaking = False
        self._cut_bot_off = False

    def is_echo(self, text: str) -> bool:
        heard = words(text)
        if len(heard) < self._min_words or self._allow & set(heard):
            return False
        return is_run_of(heard, self._spoken.recent())

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
        elif isinstance(frame, UserStartedSpeakingFrame):
            self._cut_bot_off = self._bot_speaking
        elif isinstance(frame, TranscriptionFrame) and direction == FrameDirection.DOWNSTREAM:
            if self._cut_bot_off and self.is_echo(frame.text):
                self._on_event("self_echo", text=frame.text[:120])
                return
        await self.push_frame(frame, direction)
