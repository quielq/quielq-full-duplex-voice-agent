"""Meter provider usage from Pipecat usage metrics into the cost ledger.

Fake services are free and are not recorded. When a real provider passes its daily
cap mid-session, ``on_cap_reached`` fires so the bot can say so and stop.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from pipecat.frames.frames import MetricsFrame
from pipecat.metrics.metrics import LLMUsageMetricsData, STTUsageMetricsData, TTSUsageMetricsData
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
        self._run_id = run_id
        self._on_cap_reached = on_cap_reached
        self._seen: set[int] = set()
        self.session_usd = 0.0

    async def on_push_frame(self, data: FramePushed):
        frame = data.frame
        if not isinstance(frame, MetricsFrame) or frame.id in self._seen:
            return
        self._seen.add(frame.id)
        for d in frame.data:
            for provider, kind, units, usd in self._price(d):
                self._ledger.record(provider, kind, units, usd, run_id=self._run_id)
                self.session_usd += usd
                if self._ledger.over_cap(provider) and self._on_cap_reached:
                    await self._on_cap_reached(provider)

    def _price(self, d):
        p = self._prices
        llm, tts, stt = self._stages["llm"], self._stages["tts"], self._stages["stt"]
        if isinstance(d, LLMUsageMetricsData) and llm in ("groq", "anthropic"):
            u = d.value
            yield llm, "llm_input_tokens", u.prompt_tokens, u.prompt_tokens * p[llm]["input_per_token"]
            yield llm, "llm_output_tokens", u.completion_tokens, u.completion_tokens * p[llm]["output_per_token"]
        elif isinstance(d, TTSUsageMetricsData) and tts != "fake":
            per_char = p["cartesia_per_char"] if tts == "cartesia" else p["deepgram_aura2_per_char"]
            provider = "cartesia" if tts == "cartesia" else "deepgram"
            yield provider, "tts_chars", d.value, d.value * per_char
        elif isinstance(d, STTUsageMetricsData) and stt == "deepgram_flux":
            minutes = float(d.value) / 60.0
            yield "deepgram", "stt_minutes", minutes, minutes * p["deepgram_flux_per_min"]
