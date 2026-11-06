"""Entry for the class benchmark, fd-benchmark (ADR-044).

fd-benchmark (https://github.com/trueshotjames/ai231-me3-full-duplex-benchmarking) plays the
Full-Duplex-Bench v1 and v1.5 clips into the agent over a WebSocket and scores what she says back.
Its launcher builds that socket transport and calls ``build`` here; nothing else in the bot changes:
the same VAD, Smart Turn, STT, LLM, TTS, tool policy and cost ledger run as on the mic.

Point the launcher at this file, from the project folder with fd-benchmark cloned inside it:

    FDBENCH_ENTRY=bench/fdb_adapter.py .venv/bin/python fd-benchmark/launch_assistant.py --check

Settings come from the environment, so a benchmark run never needs a project edit:

    ME3_PROFILE   mac or pi (default mac)
    ME3_STAGES    stage overrides as in ``--stages``, e.g. stt=voice_listen,llm=anthropic,tts=voice_speak
    ME3_SERVERS   task servers, default all

Switches for A/B runs (ADR-045). Unset, each keeps the profile's setting (caching on, the rest off):

    ME3_PROMPT_CACHE=0|1    Anthropic prompt caching
    ME3_ADDRESSING=1        no-wake-word addressing (ADR-016), without the level gate
    ME3_BARGE_IN=duck       duck-then-decide barge-in (ADR-007)
    ME3_FLUX=eager_eot_threshold=off   Flux settings, as in ``--flux`` (eager is on at 0.5, ADR-046)

Every stage defaults to its fake, as everywhere else. Full-Duplex-Bench data is CC BY-NC 4.0:
coursework use only.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

PROJECT_MARKER = "harness/bot.py"
SWITCHES = ("ME3_STAGES", "ME3_PROMPT_CACHE", "ME3_ADDRESSING", "ME3_BARGE_IN", "ME3_FLUX")


def _config():
    from harness.config import load_config
    from harness.providers import flux_value

    cfg = load_config(os.environ.get("ME3_PROFILE", "mac"))
    for item in filter(None, os.environ.get("ME3_STAGES", "").split(",")):
        stage, _, choice = item.partition("=")
        if stage not in cfg.providers["stages"]:
            raise SystemExit(f"ME3_STAGES: unknown stage {stage!r}")
        cfg.providers["stages"][stage] = choice
    if (cache := os.environ.get("ME3_PROMPT_CACHE")) is not None:
        if cache not in ("0", "1"):
            raise SystemExit(f"ME3_PROMPT_CACHE: {cache!r} is not 0 or 1")
        cfg.providers["providers"]["anthropic"]["prompt_caching"] = cache == "1"
    if os.environ.get("ME3_ADDRESSING") == "1":
        cfg.profile["addressing"]["enabled"] = True
        cfg.profile["addressing"]["gate"]["enabled"] = False  # unverified with Flux (ADR-016)
    if mode := os.environ.get("ME3_BARGE_IN"):
        if mode not in ("immediate", "duck"):
            raise SystemExit(f"ME3_BARGE_IN: {mode!r} is not immediate or duck")
        cfg.profile["barge_in"]["mode"] = mode
    flux = cfg.providers["providers"]["deepgram"]["flux"]
    for item in filter(None, os.environ.get("ME3_FLUX", "").split(",")):
        name, _, value = item.partition("=")
        if name not in flux:
            raise SystemExit(f"ME3_FLUX: unknown Flux setting {name!r}")
        flux[name] = flux_value(name, value)
    # The clips arrive as samples, not through a room: no echo path, so no AEC or self-echo filter.
    cfg.profile["audio"]["echo_cancel"] = False
    cfg.profile["ui"]["enabled"] = False
    os.environ["ME3_MUSIC_BACKEND"] = cfg.providers["stages"].get("music", "fake")
    return cfg


def audio_rates() -> tuple[int, int]:
    audio = _config().profile["audio"]
    return audio["in_sample_rate"], audio["out_sample_rate"]


def describe() -> dict:
    """Model versions for fdbench's results card, from the same config the run uses."""
    from harness.bot import PROMPT_VERSION

    cfg = _config()
    stages, prov = cfg.providers["stages"], cfg.providers["providers"]
    stt = {"deepgram_flux": "Deepgram", "voice_listen": "Deepgram via voice-listen MCP"}.get(stages["stt"])
    tts = {"cartesia": "Cartesia", "voice_speak": "Cartesia via voice-speak MCP"}.get(stages["tts"])
    llm = stages["llm"]
    out = {
        "ASR": f"{stt} {prov['deepgram']['stt_model']}" if stt else stages["stt"],
        "LLM": prov[llm]["model"] if llm in prov and "model" in prov[llm] else llm,
        # Pipecat 1.13's CartesiaTTSService default model; the harness does not override it.
        "TTS": f"{tts} sonic-3.6" if tts else stages["tts"],
        "VAD": "Silero VAD (Pipecat 1.13)",
        "Prompt": f"VIA {PROMPT_VERSION}",
    }
    return out


async def build(transport, log_dir: Path):
    """Return (coroutine that runs one session on ``transport``, async function that stops it)."""
    from harness.agents import AgentStore, seed_documents, session_config
    from harness.bot import run_bot
    from harness.mcp_tools import TASK_SERVERS

    cfg = _config()
    data = cfg.path("data_dir")
    seed_documents(data / "documents")
    store = AgentStore(data, cfg.providers)
    agent = store.builtins()[store.default_id]
    names = list(filter(None, os.environ.get("ME3_SERVERS", "").split(",")))
    if unknown := set(names) - set(TASK_SERVERS):
        raise SystemExit(f"ME3_SERVERS: unknown servers {sorted(unknown)}")
    servers = {n: TASK_SERVERS[n] for n in names} or None
    log_dir.mkdir(parents=True, exist_ok=True)
    # Fresh tool state per session, so a timer or note from an earlier trial or a normal run cannot ring
    # during a clip. The cost ledger stays in the real data folder, so the daily caps still hold.
    tool_data = Path(tempfile.mkdtemp(prefix="me3-fdbench-"))
    seed_documents(tool_data / "documents")

    workers: list = []

    async def run():
        result = await run_bot(session_config(cfg, agent), agent=agent, input_mode="fdbench", transport=transport,
                               on_worker=workers.append, handle_sigint=False, servers=servers,
                               data_dir=str(tool_data))  # fmt: skip
        switches = {k: os.environ[k] for k in SWITCHES if os.environ.get(k)}
        (log_dir / "last_run.txt").write_text(
            f"{result.run_id} {result.events_path} ${result.session_usd:.4f} {switches}\n"
        )
        print(f"ME3 run {result.run_id}, ${result.session_usd:.4f}, events {result.events_path}")

    async def cancel():
        if workers:
            await workers[0].cancel()

    return run(), cancel
