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
    "voice_listen": "deepgram",  # Flux through our voice-listen MCP server (ADR-032)
    "voice_speak": "cartesia",   # Cartesia through our voice-speak MCP server (ADR-032)
    "groq": "groq",
    "anthropic": "anthropic",
    "openrouter": "openrouter",
    "spotify": "spotify",
}


FLUX_STAGES = {"deepgram_flux", "voice_listen"}
CARTESIA_STAGES = {"cartesia", "voice_speak"}


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


def _relay(name: str, providers_cfg: dict, ledger: CostLedger, voice: dict | None):
    """The voice server stream for a relay stage. Its server holds the key, not the harness."""
    from harness.voice_relay import RelayClient

    choice = f"voice_{name}"
    ledger.authorize(STAGE_PROVIDER[choice])  # the server checks the cap again
    return providers_cfg["providers"][STAGE_PROVIDER[choice]], RelayClient(name, **(voice or {}))


def build_stt(
    providers_cfg: dict,
    ledger: CostLedger,
    *,
    script: Sequence[str] = (),
    interrupt: bool = True,
    forced: bool = False,
    voice: dict | None = None,
):
    """``interrupt=False`` stops a turn-detecting STT (Flux) from interrupting the bot itself,
    so the DuckingProcessor can decide instead. ``forced`` leaves ending the turn to Smart
    Turn (ADR-019), so Flux must not end one by itself or predict one. ``voice`` holds the
    MCP transport, data folder, and event log for the voice servers (ADR-032)."""
    choice = providers_cfg["stages"]["stt"]
    if choice == "fake":
        return ScriptedSTTService(script=script or ["set a timer for one minute"])
    if choice in ("deepgram_flux", "voice_listen"):
        from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService

        relay_args = {}
        if choice == "voice_listen":
            p, relay = _relay("listen", providers_cfg, ledger, voice)
            key, relay_args = "held-by-voice-listen", {"relay": relay, "relay_tool": "listen"}
        else:
            _, p, key = _authorize(choice, providers_cfg, ledger)
        # Unset values keep Deepgram's defaults. An eager threshold is what turns eager on.
        flux = {k: v for k, v in (p.get("flux") or {}).items() if v is not None}
        service = DeepgramFluxSTTService
        if forced:
            from harness.flux_forced import ForcedEndFluxSTT

            service = ForcedEndFluxSTT
            flux = {**flux, "eot_threshold": 1.0}
            flux.pop("eager_eot_threshold", None)
        if relay_args:
            from harness.voice_relay import RelayedConnection

            service = type(f"Relayed{service.__name__}", (RelayedConnection, service), {})
        return service(
            **relay_args,
            api_key=key,
            mip_opt_out=True,
            should_interrupt=interrupt,
            enable_eager_end_of_turn="eager_eot_threshold" in flux,
            settings=DeepgramFluxSTTService.Settings(model=p["stt_model"], **flux),
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
                model=p["model"], max_tokens=p["max_tokens"], system_instruction=system_instruction,
                enable_prompt_caching=bool(p.get("prompt_caching", False)),
            ),
        )
    if choice == "openrouter":
        from pipecat.services.openrouter.llm import OpenRouterLLMService

        _, p, key = _authorize(choice, providers_cfg, ledger)
        model = p["model"]
        if model not in (providers_cfg["prices"]["openrouter"].get("by_model") or {}):
            # The ledger needs a price, or the daily cap would count this model as free.
            raise SpendRefused(f"openrouter: no price for {model} in providers.yaml")
        return OpenRouterLLMService(
            api_key=key,
            settings=OpenRouterLLMService.Settings(
                model=model, max_completion_tokens=p["max_tokens"], system_instruction=system_instruction
            ),
        )
    raise ValueError(f"unknown llm stage {choice!r}")


def build_tts(providers_cfg: dict, ledger: CostLedger, *, on_event=None, hops=None, voice: dict | None = None):
    choice = providers_cfg["stages"]["tts"]
    # SENTENCE aggregation always: token aggregation commits unspoken text on interruption.
    agg = TextAggregationMode.SENTENCE
    if choice == "fake":
        return ToneTTSService(text_aggregation_mode=agg)
    if choice == "fake_words":
        return ToneTTSService(text_aggregation_mode=agg, word_timestamps=True)
    if choice == "fake_words_fast":
        # Speaks faster than the bench's pacing guess, as Cartesia does (research.md, Nov 4).
        return ToneTTSService(text_aggregation_mode=agg, word_timestamps=True, ms_per_char=45.0)
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
    if choice in ("cartesia", "voice_speak"):
        from harness.audio_hops import HopTimedTTS
        from pipecat.services.cartesia.tts import CartesiaTTSService

        class TimedCartesiaTTSService(HopTimedTTS, CartesiaTTSService):
            pass

        service, relay_args = TimedCartesiaTTSService, {}
        if choice == "voice_speak":
            from harness.voice_relay import RelayedConnection

            class RelayedCartesiaTTSService(RelayedConnection, TimedCartesiaTTSService):
                pass

            p, relay = _relay("speak", providers_cfg, ledger, voice)
            service, key = RelayedCartesiaTTSService, "held-by-voice-speak"
            relay_args = {"relay": relay, "relay_tool": "speak"}
        else:
            _, p, key = _authorize(choice, providers_cfg, ledger)
        if not p.get("voice_id"):
            raise ValueError("cartesia: set providers.cartesia.voice_id in configs/providers.yaml")
        return service(
            **relay_args,
            hops=hops,
            api_key=key,
            sample_rate=p["tts_sample_rate"],
            settings=service.Settings(voice=p["voice_id"]),
            text_aggregation_mode=agg,
        )
    raise ValueError(f"unknown tts stage {choice!r}")


def paid_providers(providers_cfg: dict) -> set[str]:
    """Providers this run would bill, for the per-session cost line."""
    return {STAGE_PROVIDER[c] for c in providers_cfg["stages"].values() if not c.startswith("fake")}
