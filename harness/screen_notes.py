"""Tell the model what was done on the screen, at the start of the next user turn (ADR-043).

A tap on the robot face runs a tool through ``ToolPolicy.run_from_screen`` (ADR-038), but the
model never saw it. In the Nov 5 live run (`20261105-195758-061828`) a tap on next track changed
the song, and when asked what was playing Haiku named the old one from its context.

Each tap that ran is held as a short note. ``ScreenNotes`` sits just before the user aggregator
and, when the next final transcript arrives, sends the notes first as one context message that
does not run the model. The aggregator then merges them with the transcript into one user turn.

The note waits for the user's turn instead of going in at once: an append lands in the context
straight away, and in the middle of a tool call it would sit between the model's tool request
and its result, which Anthropic refuses. A refused tap, or one whose tool returned an error,
changed nothing and adds no note.
"""

from __future__ import annotations

import json
from typing import Any

from pipecat.frames.frames import Frame, LLMMessagesAppendFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

MAX_NOTES = 8  # a run of taps keeps the latest ones
MAX_RESULT_CHARS = 300


def _as_dict(result: Any) -> dict | None:
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return None
    return result if isinstance(result, dict) else None


def screen_note(tool: str, args: dict, result: Any) -> str | None:
    """The note for one tap, or None when the tap changed nothing."""
    out = _as_dict(result)
    if out is None or "error" in out or out.get("status") in ("denied", "not_done"):
        return None
    shown_args = json.dumps(args, separators=(",", ":")) if args else ""
    shown = json.dumps(out, separators=(",", ":"))[:MAX_RESULT_CHARS]
    return f"{tool}({shown_args}) returned {shown}"


class ScreenNotes(FrameProcessor):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._pending: list[str] = []

    def add(self, tool: str, args: dict, result: Any) -> bool:
        note = screen_note(tool, args, result)
        if note is None:
            return False
        self._pending = [*self._pending, note][-MAX_NOTES:]
        return True

    def take(self) -> list[dict]:
        """The held notes as one context message, and forget them."""
        if not self._pending:
            return []
        lines = "\n".join(f"- {n}" for n in self._pending)
        self._pending = []
        text = ("Not said aloud: since the last reply, the user used the screen. These ran and are "
                f"now true; answer from them, not from earlier in the conversation:\n{lines}")  # fmt: skip
        return [{"role": "user", "content": text}]

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and direction == FrameDirection.DOWNSTREAM:
            if messages := self.take():
                await self.push_frame(LLMMessagesAppendFrame(messages=messages, run_llm=False), direction)
        await self.push_frame(frame, direction)
