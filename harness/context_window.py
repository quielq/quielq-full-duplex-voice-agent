"""Keep the LLM context to the last N user turns (ADR-047).

Without this the context grows for the whole session, and every turn sends all of it again.
This sits between the user aggregator and the LLM. On each ``LLMContextFrame`` going down, it
counts the user messages, and if there are more than ``keep_turns`` it drops everything before
the oldest one it keeps, in place, so the stored context shrinks too.

A cut is always made just before a plain user message, so a tool call is never split from its
result, and the kept history still starts with a user message, as Anthropic requires. The
system prompt and the tool definitions live on the LLM service and in ``context.tools``, not in
the messages, so they are never dropped. A tool call still waiting for its result is after the
last user message, so it is always kept.

Context frames that come back up from the assistant aggregator after a tool result never pass
here, so the history is never trimmed in the middle of a tool loop.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pipecat.frames.frames import Frame, LLMContextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


def _is_user_turn(message: Any) -> bool:
    """A user message that starts a turn: plain dict with role user (tool results use role tool)."""
    return isinstance(message, dict) and message.get("role") == "user"


def trim(messages: list, keep_turns: int) -> tuple[list, int]:
    """The messages from the ``keep_turns``-th last user message on, and how many were dropped."""
    starts = [i for i, m in enumerate(messages) if _is_user_turn(m)]
    if keep_turns <= 0 or len(starts) <= keep_turns:
        return messages, 0
    cut = starts[-keep_turns]
    return messages[cut:], cut


class ContextWindow(FrameProcessor):
    def __init__(self, keep_turns: int, on_event: Callable[..., None] | None = None):
        super().__init__()
        self.keep_turns = keep_turns
        self._on_event = on_event

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame) and direction == FrameDirection.DOWNSTREAM:
            messages = list(frame.context.get_messages())
            kept, dropped = trim(messages, self.keep_turns)
            if dropped:
                frame.context.set_messages(kept)
                if self._on_event:
                    self._on_event("context_trimmed", dropped_messages=dropped, kept_messages=len(kept),
                                   keep_turns=self.keep_turns)  # fmt: skip
        await self.push_frame(frame, direction)
