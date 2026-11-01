"""Local Smart Turn ends Flux turns with ForceEndTurn (spike, ADR-019).

Flux runs with ``eot_threshold=1.0``, so only ForceEndTurn or its silence timeout ends a turn.
Smart Turn alone cannot: the stock stop strategy waits for a finalized transcript, and Flux
sends one only at end of turn (ADR-005). The strategy below breaks that wait by asking for
the transcript once Smart Turn says the turn is complete.

``_maybe_trigger_user_turn_stopped`` and ``_discard_pending_end_of_turn`` are private to
Pipecat, so recheck them on every upgrade.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from loguru import logger
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy


class ForcedEndFluxSTT(DeepgramFluxSTTService):
    """Flux with a way to end the open turn."""

    async def force_end_turn(self) -> bool:
        """Returns False when there is no open turn to end: Flux never heard speech (noise)."""
        if not self._user_is_speaking or not self._transport_is_active():
            return False
        await self._transport_send_json({"type": "ForceEndTurn"})
        return True


class ForcedEndTurn:
    """Late binding: the turn strategies are built before the STT service they drive."""

    def __init__(self, on_event: Callable[..., object] = lambda *a, **k: None):
        self.stt: ForcedEndFluxSTT | None = None
        self._on_event = on_event

    async def __call__(self) -> None:
        sent = await self.stt.force_end_turn()
        self._on_event("force_end_turn", sent=sent)
        if not sent:
            logger.debug("Smart Turn said complete, but Flux has no open turn")


class ForcedEndTurnStopStrategy(TurnAnalyzerUserTurnStopStrategy):
    """Asks Flux for its final transcript when Smart Turn says the turn is complete."""

    def __init__(self, *, force_end: Callable[[], Awaitable[None]], **kwargs):
        super().__init__(**kwargs)
        self._force_end = force_end
        self._forced = False

    async def _discard_pending_end_of_turn(self):
        await super()._discard_pending_end_of_turn()
        self._forced = False

    async def _maybe_trigger_user_turn_stopped(self):
        if self._turn_complete and not self._transcript_finalized and not self._forced:
            self._forced = True
            await self._force_end()
        await super()._maybe_trigger_user_turn_stopped()
