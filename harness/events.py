"""Per-turn event log: one JSON line per event, stamped with a single monotonic clock.

The observer below records framework-level events for attribution only. Headline
latency numbers come from audio timestamps (bench/latency_probe.py), per the review.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EagerEndOfTurnCancelFrame,
    EagerTranscriptionFrame,
    ErrorFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.services.tts_service import TTSService


class EventLog:
    def __init__(self, logs_dir: Path, run_id: str | None = None):
        self.run_id = run_id or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.path = Path(logs_dir) / f"events-{self.run_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(self.path, "a", buffering=1)
        self.turn_id = 0
        self.listeners: list = []   # called with each event as it is logged (harness/ui.py)

    def emit(self, event: str, **fields: Any) -> dict:
        rec = {
            "run_id": self.run_id,
            "turn_id": self.turn_id,
            "event": event,
            "t_ns": time.monotonic_ns(),
            **fields,
        }
        self._f.write(json.dumps(rec, default=str) + "\n")
        for listener in self.listeners:
            listener(rec)
        return rec

    def next_turn(self) -> int:
        self.turn_id += 1
        return self.turn_id

    def close(self) -> None:
        self._f.close()


class TurnEventObserver(BaseObserver):
    """Turns pipeline frames into event-log lines.

    Each frame is logged once (on its first push). "first_*" events are logged
    once per turn so the latency probe can compute stage deltas.
    """

    def __init__(self, log: EventLog, **kwargs):
        super().__init__(**kwargs)
        self._log = log
        self._seen: set[int] = set()
        self._firsts: set[str] = set()
        self.spoken_text: list[str] = []
        self.transcripts: list[str] = []
        self.tool_calls: list[dict] = []
        self.llm_text: list[str] = []
        self._tool_call_ids: set[str] = set()
        self._response_has_text = False
        # Set when an LLM response that carried text has ended (used by scripted runs).
        self.reply_done = asyncio.Event()
        # Set by the bot when the waiting phrase is on (harness/filler.py).
        self.filler = None
        # Set by the bot: the undo window opens on these signals (harness/tool_policy.py).
        self.policy = None
        # Set by the bot when the face is on: reply text as it streams (harness/ui.py).
        self.on_text = None

    def _first(self, name: str, **fields) -> None:
        if name not in self._firsts:
            self._firsts.add(name)
            self._log.emit(name, **fields)

    async def on_push_frame(self, data: FramePushed):
        frame = data.frame
        # Broadcast frames travel both ways as two sibling frames; count them once.
        key = frame.broadcast_sibling_id or frame.id
        if frame.id in self._seen or key in self._seen:
            return
        self._seen.update({frame.id, key})
        log = self._log

        if isinstance(frame, VADUserStartedSpeakingFrame):
            log.emit("vad_onset")
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            log.emit("vad_end")
        elif isinstance(frame, UserStartedSpeakingFrame):
            log.next_turn()
            self._firsts.clear()
            log.emit("user_turn_start")
        elif isinstance(frame, UserStoppedSpeakingFrame):
            log.emit("end_of_turn")
        elif isinstance(frame, TranscriptionFrame):
            self.transcripts.append(frame.text)
            # Flux says why the turn ended; other STTs have no such field.
            result = frame.result if isinstance(frame.result, dict) else {}
            log.emit("asr_final", text=frame.text, trigger=result.get("trigger"))
        elif isinstance(frame, EagerTranscriptionFrame):
            log.emit("eager_eot", text=frame.text)
        elif isinstance(frame, EagerEndOfTurnCancelFrame):
            log.emit("eager_cancel")
        elif isinstance(frame, LLMFullResponseStartFrame):
            self._response_has_text = False
            log.emit("llm_request")
        elif isinstance(frame, LLMTextFrame):
            self._response_has_text = True
            self.llm_text.append(frame.text)
            self._first("llm_first_token")
            if self.on_text:
                self.on_text(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame):
            log.emit("llm_end")
            if self._response_has_text:
                self.reply_done.set()
                if self.policy:
                    self.policy.reply_ended()
        elif isinstance(frame, FunctionCallInProgressFrame):
            log.emit("tool_call_start", name=frame.function_name, args=frame.arguments)
        elif isinstance(frame, FunctionCallResultFrame):
            # The result frame is pushed both ways; log each call once.
            if frame.tool_call_id in self._tool_call_ids:
                return
            self._tool_call_ids.add(frame.tool_call_id)
            self.tool_calls.append(
                {"name": frame.function_name, "args": frame.arguments, "result": frame.result}
            )
            log.emit("tool_call_end", name=frame.function_name, result=frame.result)
        elif isinstance(frame, TTSStartedFrame):
            log.emit("tts_request")
        elif isinstance(frame, TTSAudioRawFrame):
            self._first("tts_first_byte")
        elif isinstance(frame, TTSTextFrame):
            self.spoken_text.append(frame.text)
        elif isinstance(frame, BotStartedSpeakingFrame):
            is_filler = self.filler is not None and self.filler.claim_bot_start()
            log.emit("filler_audio_out" if is_filler else "first_audio_out")
            if self.policy:
                self.policy.bot_started()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            log.emit("audio_silent")
            if self.policy:
                self.policy.bot_stopped()
        elif isinstance(frame, InterruptionFrame):
            log.emit("interruption")
        elif isinstance(frame, ErrorFrame):
            log.emit("pipeline_error", error=frame.error)
            if isinstance(data.source, TTSService):
                log.emit("tts_failed", error=frame.error)
