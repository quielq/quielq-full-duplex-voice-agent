"""Drop a spoken waiting phrase the model writes before a tool call (ADR-041).

The prompt says to call a tool with no words before it, and the device plays its own waiting
clip (ADR-033). Some models say one anyway: on Nov 4 DeepSeek V4.1 Flash said "I'll check your
documents" before its tool call. Spoken, that doubles up with the clip.

This sits between the LLM and the TTS. A reply whose first words could start a waiting phrase
(``let me``, ``I'll check``, ``one moment``, ...) is held until its first sentence ends. Then:

- a tool call in the same response drops the held sentence and logs ``preamble_dropped``;
- more text, or the end of the response, releases it unchanged.

Any other reply passes at once, so the latency of an ordinary answer does not change. The
dropped words never reach the TTS, so they stay out of the context too. Pipecat announces a
tool call with ``FunctionCallsStartedFrame``, a system frame that can overtake queued text, so
the call is remembered for the rest of the response.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from pipecat.frames.frames import (
    Frame,
    FunctionCallsStartedFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

# Lowercase openings of a waiting phrase. A reply is held only while it could still start one.
STARTS = (
    "let me",
    "i'll check",
    "i'll look",
    "i'll find",
    "i'll search",
    "i'll get",
    "i will check",
    "i will look",
    "i'm checking",
    "i'm looking",
    "checking",
    "looking that up",
    "one moment",
    "one sec",
    "just a moment",
    "just a sec",
    "give me a",
    "hold on",
    "sure, let me",
    "sure let me",
    "okay, let me",
    "okay let me",
    "ok, let me",
    "alright, let me",
)
SENTENCE_END = re.compile(r"[.!?…](\s|$)")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("’", "'")).lstrip().lower()


class PreambleFilter(FrameProcessor):
    def __init__(self, on_event: Callable[..., object] | None = None, starts=STARTS, **kwargs):
        super().__init__(**kwargs)
        self._starts = tuple(starts)
        self._on_event = on_event or (lambda *a, **k: None)
        self._reset()

    def _reset(self) -> None:
        self._held: list[LLMTextFrame] = []
        self._deciding = True  # still at the start of the response
        self._tool_called = False

    def _text(self) -> str:
        return "".join(f.text for f in self._held)

    def _could_be_preamble(self, text: str) -> bool:
        n = _norm(text)
        return any(s.startswith(n) or n.startswith(s) for s in self._starts)

    async def _release(self) -> None:
        held, self._held = self._held, []
        self._deciding = False
        for f in held:
            await self.push_frame(f, FrameDirection.DOWNSTREAM)

    def _drop(self) -> None:
        if self._held:
            self._on_event("preamble_dropped", text=self._text().strip()[:120])
        self._held = []
        self._deciding = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, LLMFullResponseStartFrame):
            tool_called = self._tool_called  # a call that overtook this start still counts
            self._reset()
            self._tool_called = tool_called
        elif isinstance(frame, FunctionCallsStartedFrame):
            self._tool_called = True
            if self._held and SENTENCE_END.search(self._text()):
                self._drop()
        elif isinstance(frame, InterruptionFrame):
            self._reset()
        elif isinstance(frame, LLMTextFrame) and self._deciding:
            await self._on_text(frame)
            return
        elif isinstance(frame, LLMFullResponseEndFrame):
            if self._held and self._tool_called:
                self._drop()
            else:
                await self._release()
            await self.push_frame(frame, direction)
            self._reset()
            return
        await self.push_frame(frame, direction)

    async def _on_text(self, frame: LLMTextFrame) -> None:
        text = self._text()
        if text and SENTENCE_END.search(text):
            # The held sentence is a waiting phrase and the reply goes on: say it after all.
            if self._tool_called:
                self._drop()
                self._held = [frame]
                self._deciding = True
                await self._recheck()
            else:
                self._held.append(frame)
                await self._release()
            return
        self._held.append(frame)
        await self._recheck()

    async def _recheck(self) -> None:
        text = self._text()
        if not self._could_be_preamble(text):
            await self._release()
        elif self._tool_called and SENTENCE_END.search(text):
            self._drop()
