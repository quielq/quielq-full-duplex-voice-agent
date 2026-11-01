"""Local Smart Turn ends Flux turns with ForceEndTurn (spike, ADR-019).

Flux runs with ``eot_threshold=1.0``, so only ForceEndTurn or its silence timeout ends a turn.
Smart Turn alone cannot: the stock stop strategy waits for a finalized transcript, and Flux
sends one only at end of turn (ADR-005). The strategy below breaks that wait by asking for
the transcript once Smart Turn says the turn is complete.

Local VAD owns the turn here, so Flux's turn proposals are dropped. A late StartOfTurn, after
VAD heard the speech end, would mark the user as speaking, and the turn controller then
refuses to stop until its 5 s watchdog fires. For the same lag, a force that arrives before
StartOfTurn waits for it.

``_maybe_trigger_user_turn_stopped``, ``_discard_pending_end_of_turn``, ``_handle_start_of_turn``
and ``_user_is_speaking`` are private to Pipecat, so recheck them on every upgrade.
"""

from __future__ import annotations

from collections.abc import Callable

from loguru import logger
from pipecat.frames.frames import ProposedUserStartedSpeakingFrame, ProposedUserStoppedSpeakingFrame
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy


class ForcedEndFluxSTT(DeepgramFluxSTTService):
    """Flux with a way to end the open turn."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._force_pending = False

    async def broadcast_frame(self, frame_cls, **kwargs):
        if frame_cls not in (ProposedUserStartedSpeakingFrame, ProposedUserStoppedSpeakingFrame):
            await super().broadcast_frame(frame_cls, **kwargs)

    async def _handle_start_of_turn(self, transcript: str):
        await super()._handle_start_of_turn(transcript)
        if self._force_pending:
            self._force_pending = False
            await self._send_force()

    async def _send_force(self) -> None:
        if self._transport_is_active():
            await self._transport_send_json({"type": "ForceEndTurn"})

    async def force_end_turn(self) -> bool:
        """Returns False when Flux has no open turn yet, so the force waits for StartOfTurn."""
        if not self._user_is_speaking:
            self._force_pending = True
            return False
        await self._send_force()
        return True

    def cancel_force(self) -> None:
        """The user spoke again, or it was only noise: do not end the next turn with it."""
        self._force_pending = False


class ForcedEndTurn:
    """Late binding: the turn strategies are built before the STT service they drive."""

    def __init__(self, on_event: Callable[..., object] = lambda *a, **k: None):
        self.stt: ForcedEndFluxSTT | None = None
        self._on_event = on_event

    async def __call__(self) -> None:
        sent = await self.stt.force_end_turn()
        self._on_event("force_end_turn", sent=sent)
        if not sent:
            logger.debug("Smart Turn said complete before Flux opened a turn")

    def cancel(self) -> None:
        if self.stt:
            self.stt.cancel_force()


class ForcedEndTurnStopStrategy(TurnAnalyzerUserTurnStopStrategy):
    """Asks Flux for its final transcript when Smart Turn says the turn is complete."""

    def __init__(self, *, force_end: ForcedEndTurn, **kwargs):
        super().__init__(**kwargs)
        self._force_end = force_end
        self._forced = False

    async def _discard_pending_end_of_turn(self):
        await super()._discard_pending_end_of_turn()
        self._forced = False
        self._force_end.cancel()

    async def _maybe_trigger_user_turn_stopped(self):
        # The stock strategy also marks a turn complete on a transcript with no VAD stop. That
        # happens after every turn, when no turn is open to end.
        if self._turn_complete and self._vad_stopped and not self._transcript_finalized and not self._forced:
            self._forced = True
            await self._force_end()
        await super()._maybe_trigger_user_turn_stopped()
