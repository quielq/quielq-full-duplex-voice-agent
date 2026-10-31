"""No-wake-word addressing: is this speech for the agent? (architecture.md, ADR-016)

Three filters, cheapest first, all off unless ``addressing.enabled`` is set:

1. ``MicGate``: a level gate on raw audio. Speech must rise above the measured noise floor
   before anything goes upstream, and a short pre-roll keeps the first word. It also owns the
   mute switch. It only closes in IDLE, so ACTIVE dialogue and barge-in always get through.
2. ``ConversationState`` and ``AddressingFilter``: in ACTIVE any speech continues the dialogue.
   In IDLE the transcript must look like a request, or carry a name, or it is dropped before
   the LLM. ``require_wake_after_idle`` raises that bar to a wake phrase.
3. ``stay_silent``: a tool in the normal LLM call, so the model can refuse with no second
   round trip and no TTS.

The word lists are hypotheses to tune against recorded rooms, not measured results.
"""

from __future__ import annotations

import collections
import math
import re
import struct
import time
from collections.abc import Callable, Sequence

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    FunctionCallResultProperties,
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.llm_service import FunctionCallParams

STAY_SILENT = "stay_silent"
ADDRESSING_PROMPT = (
    "People near you may be talking to each other. If the words are clearly not meant for you, "
    f"call {STAY_SILENT} and say nothing. When in doubt and it could be a request, answer."
)

_QUESTION_START = re.compile(
    r"^(what|what's|whats|when|when's|where|where's|who|who's|why|how|how's|which|is|are|am|do|does|did|"
    r"can|could|would|will|should|shall|may)\b",
    re.I,
)
_POLITE = re.compile(r"\b(please|can you|could you|would you|will you|i need you to|remind me)\b", re.I)
_LEADING_FILLER = re.compile(r"^(?:(?:okay|ok|alright|all right|so|um|uh|hey|hi|and|well|now)[\s,.]+)+", re.I)


class ConversationState:
    """ACTIVE for ``idle_after_s`` after the agent spoke or was spoken to, then IDLE."""

    def __init__(
        self,
        idle_after_s: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        on_event: Callable[..., object] | None = None,
    ):
        self._idle_after_s = idle_after_s
        self._clock = clock
        self._on_event = on_event or (lambda *a, **k: None)
        self._last: float | None = None
        self._speaking = False
        self._was_active = False

    @property
    def active(self) -> bool:
        active = self._speaking or (
            self._last is not None and self._clock() - self._last < self._idle_after_s
        )
        if active != self._was_active:
            self._was_active = active
            self._on_event("addressing_state", state="ACTIVE" if active else "IDLE")
        return active

    def touch(self) -> None:
        self._last = self._clock()
        _ = self.active

    def bot_speaking(self, speaking: bool) -> None:
        """The window restarts when the bot stops, so a long reply cannot end it early."""
        self._speaking = speaking
        self.touch()


def looks_addressed(
    text: str, *, min_words: int, command_verbs: Sequence[str], names: Sequence[str]
) -> str | None:
    """Why an utterance clears the IDLE bar, or None. Ordered for a useful log, not for speed."""
    words = text.split()
    low = text.lower()
    if any(re.search(rf"\b{re.escape(n)}\b", low) for n in names):
        return "name"
    if len(words) < min_words:
        return None
    body = _LEADING_FILLER.sub("", text.strip())
    if text.rstrip().endswith("?") or _QUESTION_START.match(body):
        return "question"
    if _POLITE.search(low):
        return "polite"
    first = body.split()[0].lower().strip(",.!") if body else ""
    return "command" if first in command_verbs else None


class Addressing:
    """The transcript side of the filters, shared by the voice path and text mode."""

    def __init__(self, cfg: dict, state: ConversationState, on_event: Callable[..., object] | None = None):
        self.state = state
        self._min_words: int = cfg["idle_min_words"]
        self._verbs: list[str] = [v.lower() for v in cfg["command_verbs"]]
        self._names: list[str] = [n.lower() for n in cfg["names"]]
        self._wake_only: bool = cfg["require_wake_after_idle"]
        self._wake: list[str] = [w.lower() for w in cfg["wake_phrases"]]
        self._on_event = on_event or (lambda *a, **k: None)

    def admit(self, text: str) -> bool:
        """True if this transcript should reach the LLM. Admitting one starts ACTIVE."""
        if self.state.active:
            self.state.touch()
            return True
        if self._wake_only:
            reason = "wake" if any(w in text.lower() for w in self._wake) else None
        else:
            reason = looks_addressed(
                text, min_words=self._min_words, command_verbs=self._verbs, names=self._names
            )
        if reason is None:
            self._on_event("addressing_drop", text=text)
            return False
        self._on_event("addressing_admit", reason=reason)
        self.state.touch()
        return True

    def tool(self, on_silent: Callable[[], object]) -> FunctionSchema:
        """The ``stay_silent`` tool. Its handler ends the turn: no second LLM call, so no TTS."""

        async def handler(params: FunctionCallParams) -> None:
            self._on_event("stay_silent", reason=params.arguments.get("reason", ""))
            on_silent()
            await params.result_callback(
                {"status": "silent"}, properties=FunctionCallResultProperties(run_llm=False)
            )

        return FunctionSchema(
            name=STAY_SILENT,
            description="Say nothing, because this speech was not meant for you.",
            properties={"reason": {"type": "string", "description": "A few words on why"}},
            required=[],
            handler=handler,
        )


class AddressingFilter(FrameProcessor):
    """Drops final transcripts that fail the IDLE bar, before the user aggregator sees them.

    The user turn then ends empty and no LLM call happens, the same way the DuckingProcessor
    drops a backchannel. Interim transcripts are dropped with them, so nothing leaks through.
    """

    def __init__(self, addressing: Addressing, **kwargs):
        super().__init__(**kwargs)
        self._addressing = addressing

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        state = self._addressing.state
        if isinstance(frame, BotStartedSpeakingFrame):
            state.bot_speaking(True)
        elif isinstance(frame, BotStoppedSpeakingFrame):
            state.bot_speaking(False)
        elif isinstance(frame, InterimTranscriptionFrame) and not state.active:
            return
        elif isinstance(frame, TranscriptionFrame) and not self._addressing.admit(frame.text):
            return
        await self.push_frame(frame, direction)


def rms_db(pcm: bytes) -> float:
    """Level of 16-bit mono PCM in dB relative to full scale."""
    n = len(pcm) // 2
    if n == 0:
        return -120.0
    samples = struct.unpack(f"<{n}h", pcm[: n * 2])
    power = sum(s * s for s in samples) / n
    return 10 * math.log10(power / 32768**2) if power else -120.0


class LevelGate:
    """Decides, one audio block at a time, whether speech rose above the room.

    The floor is measured over the first ``calibrate_ms``, then follows the quiet blocks
    slowly, so talking cannot raise it. Above the margin for ``open_ms`` opens the gate; below
    it for ``close_ms`` closes it. A level that stays up for ``reseat_ms``, far longer than an
    utterance, is a new noise source (a fan), so it becomes the floor and the gate closes.
    """

    def __init__(
        self,
        *,
        margin_db: float,
        open_ms: float,
        close_ms: float,
        start_floor_db: float = -60.0,
        floor_alpha: float = 0.02,
        calibrate_ms: float = 500.0,
        reseat_ms: float = 15000.0,
    ):
        self.floor_db = start_floor_db
        self.open = False
        self._margin_db = margin_db
        self._open_ms = open_ms
        self._close_ms = close_ms
        self._alpha = floor_alpha
        self._calibrate_ms = calibrate_ms
        self._reseat_ms = reseat_ms
        self._seen_ms = 0.0
        self._above_ms = 0.0
        self._below_ms = 0.0

    def update(self, level_db: float, block_ms: float) -> bool:
        if self._seen_ms < self._calibrate_ms:
            self.floor_db = level_db if self._seen_ms == 0 else min(self.floor_db, level_db)
            self._seen_ms += block_ms
            return False
        if level_db >= self.floor_db + self._margin_db:
            self._above_ms += block_ms
            self._below_ms = 0.0
            if self._above_ms >= self._reseat_ms:
                self.floor_db = level_db
                self._above_ms = 0.0
                self.open = False
            elif self._above_ms >= self._open_ms:
                self.open = True
        else:
            self._below_ms += block_ms
            self._above_ms = 0.0
            self.floor_db += self._alpha * (level_db - self.floor_db)
            if self._below_ms >= self._close_ms:
                self.open = False
        return self.open


class MicGate(FrameProcessor):
    """First processor after the mic. While closed or muted, audio stops here: no ASR bill.

    Sits before the VAD, so VAD and STT see the pre-roll as ordinary audio when the gate
    opens. ``enabled=False`` keeps the mute switch and passes everything else through.
    """

    def __init__(
        self,
        gate: LevelGate | None,
        is_active: Callable[[], bool],
        *,
        pre_roll_ms: float,
        sample_rate: int,
        on_event: Callable[..., object] | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._gate = gate
        self._is_active = is_active
        self._pre_roll_ms = pre_roll_ms
        self._rate = sample_rate
        self._ring: collections.deque[InputAudioRawFrame] = collections.deque()
        self._ring_ms = 0.0
        self._on_event = on_event or (lambda *a, **k: None)
        self._was_open = False
        self.muted = False
        self.withheld_ms = 0.0

    def toggle_mute(self) -> bool:
        self.muted = not self.muted
        self._ring.clear()
        self._ring_ms = 0.0
        self._on_event("mic_mute", muted=self.muted)
        return self.muted

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if not isinstance(frame, InputAudioRawFrame):
            await self.push_frame(frame, direction)
            return
        if self.muted:
            return
        if self._gate is None:
            await self.push_frame(frame, direction)
            return
        block_ms = len(frame.audio) / 2 / self._rate * 1000
        passes = self._gate.update(rms_db(frame.audio), block_ms) or self._is_active()
        if passes:
            if not self._was_open:
                self._on_event("gate_open", floor_db=round(self._gate.floor_db, 1), pre_roll_ms=round(self._ring_ms))
            await self._flush_ring()
            await self.push_frame(frame, direction)
        else:
            if self._was_open:
                self._on_event("gate_closed", withheld_ms=round(self.withheld_ms))
            self._keep(frame, block_ms)
        self._was_open = passes

    def _keep(self, frame: InputAudioRawFrame, block_ms: float) -> None:
        self._ring.append(frame)
        self._ring_ms += block_ms
        while self._ring_ms > self._pre_roll_ms and self._ring:
            old = self._ring.popleft()
            ms = len(old.audio) / 2 / self._rate * 1000
            self._ring_ms -= ms
            self.withheld_ms += ms

    async def _flush_ring(self) -> None:
        while self._ring:
            await self.push_frame(self._ring.popleft(), FrameDirection.DOWNSTREAM)
        self._ring_ms = 0.0
