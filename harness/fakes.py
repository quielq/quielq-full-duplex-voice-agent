"""Free, deterministic stand-ins for ASR, LLM, and TTS. These are the default for every stage.

* ``ScriptedSTTService`` runs on real VAD segments and returns the next scripted line.
* ``RuleBasedLLMService`` calls ``set_timer`` (or other advertised tools) for requests it
  recognises, then confirms from the tool result. It never touches the network.
* ``ToneTTSService`` yields a quiet tone whose length tracks the text, sentence by sentence.
"""

from __future__ import annotations

import dataclasses
import json
import math
import re
import struct
import uuid
from collections.abc import AsyncGenerator, Sequence

from pipecat.frames.frames import (
    Frame,
    FunctionCallFromLLM,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import LLMService
from pipecat.services.settings import LLMSettings, STTSettings, TTSSettings
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.services.tts_service import TextAggregationMode, TTSService
from pipecat.utils.time import time_now_iso8601


def _unsupported(cls, **given):
    """Settings object with every field None (unsupported) except the ones given."""
    names = [f.name for f in dataclasses.fields(cls) if f.name != "extra"]
    return cls(**{n: None for n in names} | given)


class ScriptedSTTService(SegmentedSTTService):
    """Returns scripted lines, one per VAD speech segment, cycling when it runs out."""

    def __init__(self, script: Sequence[str], **kwargs):
        kwargs.setdefault("settings", _unsupported(STTSettings, model="fake-scripted"))
        super().__init__(**kwargs)
        self._script = list(script) or ["hello"]
        self._i = 0

    @property
    def wants_wav_segments(self) -> bool:
        return False

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None, None]:
        text = self._script[self._i % len(self._script)]
        self._i += 1
        yield TranscriptionFrame(text=text, user_id="", timestamp=time_now_iso8601())


_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "forty-five": 45,
    "fifty": 50, "sixty": 60, "ninety": 90,
}  # fmt: skip
_UNIT_SECONDS = {"second": 1, "minute": 60, "hour": 3600}
_TIMER_RE = re.compile(
    r"timer for (?:(\d+)|([a-z-]+)) (second|minute|hour)s?", re.IGNORECASE
)


def parse_timer_request(text: str) -> int | None:
    """Return the requested duration in seconds, or None. Uses the last value, so
    self-corrections like "ten, no, fifteen minutes" are a later concern for the real LLM."""
    m = _TIMER_RE.search(text)
    if not m:
        return None
    n = int(m.group(1)) if m.group(1) else _NUMBER_WORDS.get(m.group(2).lower())
    if n is None:
        return None
    return n * _UNIT_SECONDS[m.group(3).lower()]


def say_duration(seconds: int) -> str:
    for unit, size in (("hour", 3600), ("minute", 60), ("second", 1)):
        if seconds % size == 0 and seconds >= size:
            n = seconds // size
            words = {1: "one", 2: "two", 3: "three", 5: "five", 10: "ten"}
            return f"{words.get(n, str(n))} {unit}{'' if n == 1 else 's'}"
    return f"{seconds} seconds"


def _tool_names(context) -> set[str]:
    tools = getattr(context, "tools", None)
    standard = getattr(tools, "standard_tools", None) or []
    return {t.name for t in standard}


class RuleBasedLLMService(LLMService):
    """A scripted LLM. Speaks in short sentences and streams word by word."""

    def __init__(self, **kwargs):
        kwargs.setdefault("settings", _unsupported(LLMSettings, model="fake-rules"))
        super().__init__(**kwargs)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame):
            await self.push_frame(LLMFullResponseStartFrame())
            await self._respond(frame.context)
            await self.push_frame(LLMFullResponseEndFrame())
        else:
            await self.push_frame(frame, direction)

    async def _stream(self, text: str) -> None:
        for i, word in enumerate(text.split(" ")):
            await self._push_llm_text(word if i == 0 else " " + word)

    async def _respond(self, context) -> None:
        messages = context.get_messages()
        last = messages[-1] if messages else {}
        role = last.get("role")

        if role == "tool":
            await self._stream(self._confirm(last.get("content")))
            return

        text = last.get("content") if role == "user" else ""
        if isinstance(text, list):  # content parts
            text = " ".join(p.get("text", "") for p in text if isinstance(p, dict))
        seconds = parse_timer_request(text or "")
        if seconds is not None and "set_timer" in _tool_names(context):
            await self.run_function_calls(
                [
                    FunctionCallFromLLM(
                        function_name="set_timer",
                        tool_call_id=f"call_{uuid.uuid4().hex[:12]}",
                        arguments={"duration_seconds": seconds, "label": ""},
                        context=context,
                    )
                ]
            )
            return
        await self._stream(f"I heard: {text}." if text else "Sorry, could you repeat that?")

    @staticmethod
    def _confirm(content) -> str:
        # MCP tool results reach the context as a JSON-encoded string of JSON text,
        # so decode until it stops being a string.
        result = content
        try:
            for _ in range(3):
                if not isinstance(result, str):
                    break
                result = json.loads(result)
        except json.JSONDecodeError:
            return "Done."
        if isinstance(result, dict) and "remaining_seconds" in result:
            return f"Done. Timer set for {say_duration(int(result['remaining_seconds']))}."
        if isinstance(result, dict) and result.get("error"):
            return "Sorry, that did not work."
        return "Done."


class ToneTTSService(TTSService):
    """Quiet 440 Hz tone, about 60 ms per character, so playback length tracks text length."""

    def __init__(self, *, ms_per_char: float = 60.0, **kwargs):
        kwargs.setdefault("text_aggregation_mode", TextAggregationMode.SENTENCE)
        kwargs.setdefault("settings", _unsupported(TTSSettings, model="fake-tone"))
        super().__init__(**kwargs)
        self._ms_per_char = ms_per_char

    def can_generate_metrics(self) -> bool:
        return True

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        sr = self.sample_rate
        total = int(sr * len(text) * self._ms_per_char / 1000)
        chunk = sr // 50  # 20 ms frames
        await self.start_ttfb_metrics()
        for start in range(0, total, chunk):
            n = min(chunk, total - start)
            samples = (
                int(1500 * math.sin(2 * math.pi * 440 * (start + k) / sr)) for k in range(n)
            )
            audio = struct.pack(f"<{n}h", *samples)
            await self.stop_ttfb_metrics()
            yield TTSAudioRawFrame(audio=audio, sample_rate=sr, num_channels=1, context_id=context_id)
