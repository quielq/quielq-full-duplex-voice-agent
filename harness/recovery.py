"""Say something when the LLM fails. Without this the bot goes quiet and the user waits.

Groq rejected a ``set_timer`` of 0 seconds at schema validation (run 20261031-153429-ccce0b):
Pipecat pushed an ErrorFrame, no reply followed, and nothing was spoken. The phrase here is
fixed text with no LLM call, spoken at most once per turn. Pipeline errors stay in the event
log either way (``pipeline_error``, see bench/run_report.py).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from pipecat.frames.frames import ErrorFrame
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.services.llm_service import LLMService


class LLMErrorRecovery(BaseObserver):
    def __init__(
        self,
        phrase: str,
        speak: Callable[[str], Awaitable[None]],
        turn_id: Callable[[], int],
        on_event: Callable[..., object] | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._phrase = phrase
        self._speak = speak
        self._turn_id = turn_id
        self._on_event = on_event or (lambda *a, **k: None)
        self._spoken_turn: int | None = None

    async def on_push_frame(self, data: FramePushed):
        if not isinstance(data.frame, ErrorFrame) or not isinstance(data.source, LLMService):
            return
        turn = self._turn_id()
        if turn == self._spoken_turn:
            return
        self._spoken_turn = turn
        self._on_event("llm_error_spoken", phrase=self._phrase)
        await self._speak(self._phrase)
