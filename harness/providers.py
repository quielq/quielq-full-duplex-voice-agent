"""Build the STT, LLM, and TTS services for a run.

Fakes are the default. A real provider is built only if the stage selects it, its key is
present, and the cost ledger authorizes it (cap above zero and not yet reached).
Real-provider imports are lazy so a fake-only run never loads a provider SDK.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from bench.cost_ledger import CostLedger, SpendRefused
from harness.fakes import RuleBasedLLMService, ScriptedSTTService, ToneTTSService
from pipecat.services.tts_service import TextAggregationMode

STAGE_PROVIDER = {
    "deepgram_flux": "deepgram",
    "deepgram_aura2": "deepgram",
    "cartesia": "cartesia",
    "groq": "groq",
    "anthropic": "anthropic",
}


def _key(providers_cfg: dict, provider: str) -> str:
    env_key = providers_cfg["providers"][provider]["env_key"]
    value = os.environ.get(env_key, "")
    if not value:
        raise SpendRefused(f"{provider}: {env_key} is not set in .env")
    return value


def _authorize(choice: str, providers_cfg: dict, ledger: CostLedger) -> tuple[str, dict, str]:
    provider = STAGE_PROVIDER[choice]
    ledger.authorize(provider)
    return provider, providers_cfg["providers"][provider], _key(providers_cfg, provider)


def build_stt(
    providers_cfg: dict, ledger: CostLedger, *, script: Sequence[str] = (), interrupt: bool = True
):
    """``interrupt=False`` stops a turn-detecting STT (Flux) from interrupting the bot itself,
    so the DuckingProcessor can decide instead."""
    choice = providers_cfg["stages"]["stt"]
    if choice == "fake":
        return ScriptedSTTService(script=script or ["set a timer for one minute"])
    if choice == "deepgram_flux":
        from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService

        _, p, key = _authorize(choice, providers_cfg, ledger)
        return DeepgramFluxSTTService(
            api_key=key,
            mip_opt_out=True,
            should_interrupt=interrupt,
            settings=DeepgramFluxSTTService.Settings(model=p["stt_model"]),
        )
    raise ValueError(f"unknown stt stage {choice!r}")


def build_llm(providers_cfg: dict, ledger: CostLedger, *, system_instruction: str | None = None):
    choice = providers_cfg["stages"]["llm"]
    if choice == "fake":
        return RuleBasedLLMService()
    if choice == "groq":
        from pipecat.services.groq.llm import GroqLLMService

        _, p, key = _authorize(choice, providers_cfg, ledger)
        return GroqLLMService(
            api_key=key,
            settings=GroqLLMService.Settings(
                model=p["model"], reasoning_effort="low", system_instruction=system_instruction
            ),
        )
    if choice == "anthropic":
        from pipecat.services.anthropic.llm import AnthropicLLMService

        _, p, key = _authorize(choice, providers_cfg, ledger)
        return AnthropicLLMService(
            api_key=key,
            settings=AnthropicLLMService.Settings(
                model=p["model"], system_instruction=system_instruction
            ),
        )
    raise ValueError(f"unknown llm stage {choice!r}")


def build_tts(providers_cfg: dict, ledger: CostLedger, *, on_event=None, hops=None):
    choice = providers_cfg["stages"]["tts"]
    # SENTENCE aggregation always: token aggregation commits unspoken text on interruption.
    agg = TextAggregationMode.SENTENCE
    if choice == "fake":
        return ToneTTSService(text_aggregation_mode=agg)
    if choice == "fake_words":
        return ToneTTSService(text_aggregation_mode=agg, word_timestamps=True)
    if choice == "deepgram_aura2":
        from harness.deepgram_tts import FreshSocketDeepgramTTSService

        _, p, key = _authorize(choice, providers_cfg, ledger)
        return FreshSocketDeepgramTTSService(
            on_event=on_event,
            hops=hops,
            api_key=key,
            sample_rate=p["tts_sample_rate"],
            mip_opt_out=True,
            settings=FreshSocketDeepgramTTSService.Settings(voice=p["tts_voice"]),
            text_aggregation_mode=agg,
        )
    if choice == "cartesia":
        from pipecat.services.cartesia.tts import CartesiaTTSService

        _, p, key = _authorize(choice, providers_cfg, ledger)
        if not p.get("voice_id"):
            raise ValueError("cartesia: set providers.cartesia.voice_id in configs/providers.yaml")
        return CartesiaTTSService(
            api_key=key,
            settings=CartesiaTTSService.Settings(voice=p["voice_id"]),
            text_aggregation_mode=agg,
        )
    raise ValueError(f"unknown tts stage {choice!r}")


def paid_providers(providers_cfg: dict) -> set[str]:
    """Providers this run would bill, for the per-session cost line."""
    return {STAGE_PROVIDER[c] for c in providers_cfg["stages"].values() if not c.startswith("fake")}
