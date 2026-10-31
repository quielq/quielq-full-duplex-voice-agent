"""Meter provider usage into the cost ledger.

LLM tokens and STT audio seconds come from Pipecat usage metrics. TTS characters are
counted here from the text sent to TTS (LLM text and TTSSpeakFrame), because Pipecat's
Aura-2 WebSocket service emits no TTS usage metric (found Oct 21, 2026). This counts the
whole reply even if it is interrupted, which errs high, as a cap should.

Fake services are free and are not recorded. When a real provider passes its daily
cap mid-session, ``on_cap_reached`` fires so the bot can say so and stop.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from pipecat.frames.frames import LLMFullResponseEndFrame, LLMTextFrame, MetricsFrame, TTSSpeakFrame
from pipecat.metrics.metrics import LLMUsageMetricsData, STTUsageMetricsData
from pipecat.observers.base_observer import BaseObserver, FramePushed

from bench.cost_ledger import CostLedger


class CostObserver(BaseObserver):
    def __init__(
        self,
        ledger: CostLedger,
        providers_cfg: dict,
        run_id: str,
        on_cap_reached: Callable[[str], Awaitable[None]] | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._ledger = ledger
        self._prices = providers_cfg["prices"]
        self._stages = providers_cfg["stages"]
        self._models = providers_cfg["providers"]
        self._run_id = run_id
        self._on_cap_reached = on_cap_reached
        self._seen: set[int] = set()
        self._tts_chars_pending = 0
        self.session_usd = 0.0

    async def on_push_frame(self, data: FramePushed):
        frame = data.frame
        if frame.id in self._seen:
            return
        if isinstance(frame, MetricsFrame):
            self._seen.add(frame.id)
            for d in frame.data:
                for item in self._price(d):
                    await self._record(*item)
        elif isinstance(frame, (LLMTextFrame, TTSSpeakFrame)) and data.first_push:
            # Count text once, where it is first pushed (the TTS may re-push copies).
            self._seen.add(frame.id)
            self._tts_chars_pending += len(frame.text)
            if isinstance(frame, TTSSpeakFrame):
                await self.flush_tts()
        elif isinstance(frame, LLMFullResponseEndFrame):
            self._seen.add(frame.id)
            await self.flush_tts()

    async def flush_tts(self) -> None:
        """Record TTS characters counted since the last flush (end of each LLM response)."""
        chars, self._tts_chars_pending = self._tts_chars_pending, 0
        tts = self._stages["tts"]
        if chars == 0 or tts.startswith("fake"):
            return
        if tts == "cartesia":
            await self._record("cartesia", "tts_chars", chars, chars * self._prices["cartesia_per_char"])
        else:
            await self._record("deepgram", "tts_chars", chars, chars * self._prices["deepgram_aura2_per_char"])

    async def _record(self, provider: str, kind: str, units: float, usd: float) -> None:
        self._ledger.record(provider, kind, units, usd, run_id=self._run_id)
        self.session_usd += usd
        if self._ledger.over_cap(provider) and self._on_cap_reached:
            await self._on_cap_reached(provider)

    def _price(self, d):
        p = self._prices
        llm, stt = self._stages["llm"], self._stages["stt"]
        if isinstance(d, LLMUsageMetricsData) and llm in ("groq", "anthropic"):
            u = d.value
            rate = p[llm].get("by_model", {}).get(self._models[llm].get("model"), p[llm])
            yield llm, "llm_input_tokens", u.prompt_tokens, u.prompt_tokens * rate["input_per_token"]
            yield llm, "llm_output_tokens", u.completion_tokens, u.completion_tokens * rate["output_per_token"]
        elif isinstance(d, STTUsageMetricsData) and stt == "deepgram_flux":
            minutes = float(d.value.audio_seconds) / 60.0
            yield "deepgram", "stt_minutes", minutes, minutes * p["deepgram_flux_per_min"]
