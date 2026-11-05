"""ME3 voice agent entry point. One command, two profiles.

    uv run python -m harness.bot --profile mac                       # headphones, live mic
    uv run python -m harness.bot --profile mac --input wav --wav x.wav --mcp http
    uv run python -m harness.bot --profile mac --input text --say "set a timer for one minute"

Inputs:
  mic   LocalAudioTransport (headphones in Phase 1). Runs until Ctrl-C.
  wav   A WAV file streamed in real time through VAD, Smart Turn, and STT. Ends after the reply.
  text  Skips audio input and ASR: the request goes straight to the LLM. Ends after the reply.

Every stage uses a free fake unless configs/providers.yaml selects a real provider
whose daily cap is above zero.

Agents (ADR-039): --agent picks one for a scripted run. With the robot face on a live mic, the
face picks it instead, and choosing another ends the session and starts the next one with it.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import signal
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import LLMMessagesAppendFrame, TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.turns.user_start import TranscriptionUserTurnStartStrategy, VADUserTurnStartStrategy
from pipecat.turns.user_stop import (
    SpeechTimeoutUserTurnStopStrategy,
    TurnAnalyzerUserTurnStopStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.workers.runner import WorkerRunner

from bench.cost_ledger import CostLedger
from harness.agents import DOCUMENT_TOOLS, Agent, AgentStore, prompt_for, seed_documents, session_config
from harness.addressing import (
    ADDRESSING_PROMPT,
    Addressing,
    AddressingFilter,
    ConversationState,
    LevelGate,
    MicGate,
)
from harness.audio_hops import AudioHops, trace_writes
from harness.audio_marks import InputVoiceTap, OutputVoiceTap, stamp_capture
from harness.audio_io import EchoRisk, local_transport
from harness.config import Config, load_config
from harness.costs import CostObserver
from harness.ducking import DuckGain, DuckingProcessor
from harness.events import EventLog, TurnEventObserver
from harness.filler import FillerProcessor
from harness.preamble import PreambleFilter
from harness.self_echo import SelfEchoFilter, SpokenTap, SpokenWords
from harness.file_input import ScriptedMicSource, Segment
from harness.mcp_tools import TASK_SERVERS, task_clients
from harness.network import ClipPlayer, NetworkWatch, stage_hosts
from harness.null_output import PacedNullOutputTransport
from harness.phrase_cache import load_phrase
from harness.flux_forced import ForcedEndTurn, ForcedEndTurnStopStrategy
from harness.providers import FLUX_STAGES, build_llm, build_stt, build_tts
from harness.system_stats import LoopLag, SystemStats
from servers.tasks.music.backends import PLAYER_NAME, SpotifyBackend
from harness.recovery import LLMErrorRecovery
from harness.delegate_watch import DelegateWatch
from harness.timer_watch import TimerWatch
from harness.tool_policy import PolicyGate, ToolPolicy
from harness.ui import UiServer, UiState

SYSTEM_PROMPT = (
    "You are a hands-free voice assistant. Replies are spoken aloud, so keep them to one or "
    "two short sentences with no lists or formatting. That limit is for speech, not for what "
    "you write into files. Use the tools when a request needs them. "
    "Users never know ids: find an id with a list tool, and never ask for one. "
    "If no tool can do what was asked, say so; never claim you did it. "
    "If a tool result says waiting_for_undo or needs_confirmation, it has not happened yet: say so "
    "briefly and do not call the tool again. "
    "File tools write only to the workspace folder. The documents folder is read-only, so never "
    "say you put a file there, and give folder paths only from a tool result. "
    "To read a web page use fetch_content, never bash: bash has no network access. "
    "To show an HTML file use open_in_browser, never bash: bash has no desktop. "
    "To delete files or folders, call bash with rm when the user asks: the device asks them first "
    "and keeps a copy of everything deleted, so it is never permanent. Do not refuse or ask yourself. "
    "Call a tool straight away, with no words before it. "
    "Never open a reply with a waiting phrase such as one moment, let me check, or on it: the "
    "device already plays one while you work, so start with the answer. "
    "The one exception is a job of several steps, such as planning and building a website: say in "
    "one short sentence what you will make, then keep calling tools until it is finished, without "
    "asking whether to continue. Give the job its own folder and write a short plan.md there "
    "first. Keep each file under about 300 lines, and use append mode for a longer one. Check the "
    "result with read_file, then say in one sentence that it is done and offer to open it. "
    "Requests come from speech recognition, so words may be misheard: act on the likely meaning "
    "without remarking on it. If a short fragment could mean different things, ask in a few words "
    "and do not act on it. "
    "Say years and numbers as plain words in a sentence, with no parentheses, and say file names "
    "rather than full paths unless asked for the path."
)


@dataclass
class BotResult:
    run_id: str
    events_path: Path
    transcripts: list[str] = field(default_factory=list)
    llm_text: str = ""
    spoken_text: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    session_usd: float = 0.0
    policy_decisions: list[dict] = field(default_factory=list)
    context_messages: list[dict] = field(default_factory=list)
    output: PacedNullOutputTransport | None = None


def _turn_strategies(cfg: Config, force_end: ForcedEndTurn | None = None) -> UserTurnStrategies | None:
    """Turn detection from profile ``turn.stop``: auto | smart_turn | flux | flux_forced | speech_timeout.

    "auto" is flux when the STT is Deepgram Flux and smart_turn otherwise. Flux has no
    finalize message, so Smart Turn cannot end a Flux turn early, and mixing the two
    stalled a turn in testing (ADR-005).

    Pipecat 1.x API: interruptions and turn ends come from user_turn_strategies.
    Never allow_interruptions / interruption_strategies / TransportParams(vad_analyzer=...).
    "flux" returns None so the aggregator applies the strategies Deepgram Flux
    recommends (its own start and end of turn); it needs Flux (stt=deepgram_flux or voice_listen).
    """
    mode = cfg.profile["turn"]["stop"]
    flux_stt = cfg.providers["stages"]["stt"] in FLUX_STAGES
    if mode == "auto":
        mode = "flux" if flux_stt else "smart_turn"
    elif mode not in ("flux", "flux_forced") and flux_stt:
        raise ValueError(f"turn.stop={mode} does not work with Deepgram Flux STT; use auto or flux")
    elif mode == "flux_forced" and not flux_stt:
        raise ValueError("turn.stop=flux_forced needs stages.stt=deepgram_flux or voice_listen")
    if mode == "flux":
        if not flux_stt:
            raise ValueError("turn.stop=flux needs stages.stt=deepgram_flux or voice_listen")
        return None
    if mode == "smart_turn":
        stop = [TurnAnalyzerUserTurnStopStrategy(turn_analyzer=LocalSmartTurnAnalyzerV3())]
    elif mode == "flux_forced":
        stop = [ForcedEndTurnStopStrategy(turn_analyzer=LocalSmartTurnAnalyzerV3(), force_end=force_end)]
    elif mode == "speech_timeout":
        stop = [SpeechTimeoutUserTurnStopStrategy()]
    else:
        raise ValueError(f"unknown turn.stop {mode!r}")
    # With duck-then-decide the DuckingProcessor interrupts, not the turn start.
    interrupt = cfg.profile["barge_in"]["mode"] == "immediate"
    return UserTurnStrategies(
        start=[
            VADUserTurnStartStrategy(enable_interruptions=interrupt),
            TranscriptionUserTurnStartStrategy(enable_interruptions=interrupt),
        ],
        stop=stop,
    )


def _ducking(cfg: Config, log: EventLog) -> tuple[DuckGain | None, list]:
    b = cfg.profile["barge_in"]
    if b["mode"] == "immediate":
        return None, []
    if b["mode"] != "duck":
        raise ValueError(f"unknown barge_in.mode {b['mode']!r}")
    gain = DuckGain(on_event=log.emit)
    proc = DuckingProcessor(
        gain,
        gain=b["duck_gain"],
        backchannel_max_s=b["backchannel_max_s"],
        backchannel_max_words=b["backchannel_max_words"],
        allowlist=b["allowlist"],
        on_event=log.emit,
    )
    return gain, [proc]


def _addressing(cfg: Config, log: EventLog) -> Addressing | None:
    a = cfg.profile["addressing"]
    if not a["enabled"]:
        return None
    return Addressing(a, ConversationState(a["idle_after_s"], on_event=log.emit), on_event=log.emit)


def _mic_gate(cfg: Config, log: EventLog, addressing: Addressing | None) -> MicGate:
    g = cfg.profile["addressing"]["gate"]
    level = None
    if addressing and g["enabled"]:
        level = LevelGate(margin_db=g["margin_db"], open_ms=g["open_ms"], close_ms=g["close_ms"])
    is_active = (lambda: addressing.state.active) if addressing else (lambda: True)
    return MicGate(level, is_active, pre_roll_ms=g["pre_roll_ms"],
                   sample_rate=cfg.profile["audio"]["in_sample_rate"], on_event=log.emit)  # fmt: skip


def _filler(cfg: Config, log: EventLog, addressing: Addressing | None = None) -> FillerProcessor | None:
    f = cfg.profile["filler"]
    if not f["enabled"]:
        return None
    rate = cfg.profile["audio"]["out_sample_rate"]
    names = f.get("phrases") or [f["phrase"]]
    loaded = [load_phrase(name, rate) for name in names]
    clips = [pcm for pcm, real in loaded if real] or [loaded[0][0]]  # a tone when none exist yet
    missing = [n for n, (_, real) in zip(names, loaded) if not real]
    if missing:
        logger.warning(f"no clip for {', '.join(missing)}: run bench.make_phrases (paid), or hear a tone")
    log.emit("filler_clip", phrases=names, real=len(names) - len(missing))
    is_active = (lambda: addressing.state.active) if addressing else (lambda: True)
    return FillerProcessor(clips, rate, after_ms=f["after_ms"], on_event=log.emit, is_active=is_active,
                           slow_tools=f.get("slow_tools"), after_tool_ms=f.get("after_tool_ms"))  # fmt: skip


def _vad(cfg: Config) -> VADProcessor:
    v = cfg.profile["vad"]
    params = VADParams(
        confidence=v["confidence"],
        start_secs=v["start_secs"],
        stop_secs=v["stop_secs"],
        min_volume=v["min_volume"],
    )
    # A standalone VAD processor (rather than the aggregator's vad_analyzer) puts VAD
    # frames upstream of STT and, later, the DuckingProcessor.
    return VADProcessor(vad_analyzer=SileroVADAnalyzer(params=params))


async def _assistant_committed(context: LLMContext, timeout_s: float = 2.0) -> None:
    """In text mode nothing paces the next turn, so let the reply reach the context first."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        messages = context.get_messages()
        if messages and messages[-1].get("role") == "assistant" and messages[-1].get("content"):
            return
        await asyncio.sleep(0.01)


async def run_bot(
    cfg: Config,
    *,
    input_mode: str = "mic",
    mcp_transport: str = "stdio",
    say: str = "set a timer for one minute",
    turns: Sequence[str | float] | None = None,
    wav: Path | None = None,
    segments: list[Segment] | None = None,
    stt_script: list[str] | None = None,
    data_dir: str | None = None,
    reply_timeout_s: float = 60.0,
    last_reply_timeout_s: float | None = None,
    handle_sigint: bool = True,
    servers: dict[str, str] | None = None,
    agent: Agent | None = None,
    ui: UiServer | None = None,
) -> BotResult:
    """One session. ``ui`` is a face that outlives the session (the agent loop in main); without
    it the session starts and stops its own face when the profile turns the face on."""
    log = EventLog(cfg.path("logs_dir"))
    doc_scope = None
    if agent is not None:
        log.emit("agent", id=agent.id, kind=agent.kind, model=agent.model, documents=agent.documents)
        if agent.kind == "documents":
            servers, doc_scope = {"docs": TASK_SERVERS["docs"]}, agent.documents
    ledger = CostLedger.from_providers_config(
        cfg.providers, cfg.path("data_dir") / "cost_ledger.jsonl"
    )
    turn_obs = TurnEventObserver(log)
    worker: PipelineWorker | None = None

    cap_hit = False
    clips: ClipPlayer | None = None

    async def on_cap_reached(provider: str) -> None:
        nonlocal cap_hit
        if cap_hit:
            return
        cap_hit = True
        log.emit("spend_cap_reached", provider=provider)
        logger.warning(f"{provider}: daily spend cap reached, stopping the agent")
        if worker is None:
            return
        # A stored clip, so announcing the cap never spends past it, whichever provider hit it.
        if clips is not None:
            await clips.play(cfg.profile["recovery"]["cap_clip"])
        await worker.stop_when_done()

    cost_obs = CostObserver(ledger, cfg.providers, log.run_id, on_cap_reached)
    log.emit("run_start", profile=cfg.name, input=input_mode, mcp=mcp_transport,
             stages=cfg.providers["stages"])  # fmt: skip

    policy = ToolPolicy(cfg.tool_policy, on_event=log.emit)
    policy.plays_audio = input_mode != "text"
    turn_obs.policy = policy

    ui_cfg = cfg.profile["ui"]
    owns_ui = ui is None and ui_cfg["enabled"]
    if owns_ui:
        ui = UiServer(UiState(Path(data_dir) if data_dir else cfg.path("data_dir")), ui_cfg["host"], ui_cfg["port"])
        await ui.start()
        logger.info(f"robot face at http://{ui_cfg['host']}:{ui_cfg['port']}")
        if ui_cfg["spotify_player"]:
            _spotify_player(ui, log)
    if ui:
        log.listeners.append(ui.state.on_event)
        turn_obs.on_text = ui.state.on_text
        if agent is not None:
            ui.state.set_agent(agent)

    addressing = _addressing(cfg, log)
    stayed_silent = False

    def on_silent() -> None:
        nonlocal stayed_silent
        stayed_silent = True
        turn_obs.reply_done.set()

    last_step = time.monotonic()

    def shutdown(step: str) -> None:
        """Time each exit step: one live run took 62 s to stop (20261102-095054-8e433b)."""
        nonlocal last_step
        now = time.monotonic()
        log.emit("shutdown_step", step=step, ms=round((now - last_step) * 1000))
        last_step = now

    async with task_clients(mcp_transport, data_dir, servers, doc_scope) as clients:
        # The system prompt lives on the LLM service (system messages in LLMContext are
        # deprecated since Pipecat 1.9).
        tools = await policy.tools(clients)
        if doc_scope is not None:  # a document agent sees its search tools and nothing else
            tools = ToolsSchema(standard_tools=[t for t in tools.standard_tools if t.name in DOCUMENT_TOOLS])
        if addressing:
            tools.standard_tools.append(addressing.tool(on_silent))
        context = LLMContext(tools=tools)
        forced = cfg.profile["turn"]["stop"] == "flux_forced"
        force_end = ForcedEndTurn(log.emit) if forced else None
        user_params = LLMUserAggregatorParams(user_turn_strategies=_turn_strategies(cfg, force_end))
        if addressing:
            # Pipecat's default asks the LLM to request a repeat after an empty turn, which is
            # exactly what a dropped utterance leaves behind (ADR-016).
            user_params.empty_user_turn = None
        aggregators = LLMContextAggregatorPair(context, user_params=user_params)
        base_prompt = prompt_for(agent, SYSTEM_PROMPT) if agent else SYSTEM_PROMPT
        prompt = f"{base_prompt} {ADDRESSING_PROMPT}" if addressing else base_prompt
        llm = build_llm(cfg.providers, ledger, system_instruction=prompt)
        hops = AudioHops(log.emit)
        voice = {"transport": mcp_transport, "data_dir": str(cfg.path("data_dir")), "on_event": log.emit}
        tts = build_tts(cfg.providers, ledger, on_event=log.emit, hops=hops, voice=voice)
        llm.register_function(None, policy.deny_unknown)  # disabled or invented tools stop here

        audio = cfg.profile["audio"]
        source = output = None
        duck_gain, duck_procs = _ducking(cfg, log)
        stt_interrupts = duck_gain is None
        gate = PolicyGate(policy, on_spoken=turn_obs.reply_done.set)
        address_procs = [AddressingFilter(addressing)] if addressing else []
        echo_procs: list = []
        tap_procs: list = []
        se = cfg.profile.get("self_echo") or {}
        if audio["echo_cancel"] and se.get("enabled", True) and input_mode != "text":
            # Speakers leave some echo: do not answer the bot's own words (ADR-042).
            spoken = SpokenWords(window_s=se.get("window_s", 15.0))
            allow = cfg.profile["barge_in"]["allowlist"]
            echo_procs = [SelfEchoFilter(spoken, min_words=se.get("min_words", 3), allowlist=allow, on_event=log.emit)]
            tap_procs = [SpokenTap(spoken)]
        mic_gate = _mic_gate(cfg, log, addressing)
        filler = _filler(cfg, log, addressing)
        filler_procs = [filler] if filler else []
        turn_obs.filler = filler
        net_cfg = cfg.profile["network"]
        network: NetworkWatch | None = None
        clips = ClipPlayer(audio["out_sample_rate"], muted=lambda: network is not None and network.down,
                           on_event=log.emit)  # fmt: skip
        turn_obs.clips = clips
        if net_cfg["enabled"]:
            network = NetworkWatch(
                stage_hosts(cfg.providers["stages"]),
                play=lambda: clips.play(cfg.profile["recovery"]["network_clip"]),
                on_turn_lost=lambda stage: turn_lost(stage),
                on_event=log.emit,
                timeout_s=net_cfg["probe_timeout_s"],
                attempts=net_cfg["probe_attempts"],
                backoff_s=net_cfg["backoff_s"],
            )

        def turn_lost(stage: str) -> None:
            """A turn the outage swallowed: end it, and forget a request the LLM never answered."""
            messages = list(context.get_messages())  # set_messages refills the same list
            if stage == "llm" and messages and messages[-1].get("role") == "user":
                context.set_messages(messages[:-1])
                log.emit("lost_turn_dropped", text=str(messages[-1].get("content", ""))[:200])
            turn_obs.reply_done.set()

        if input_mode == "mic":
            transport = local_transport(audio, audio_out_filter=duck_gain, on_event=log.emit)
            trace_writes(transport.output(), hops)
            # Headline latency comes from these sample-level marks (ADR-036).
            OutputVoiceTap(log.emit).attach(transport.output())
            stamp_capture(transport.input())
            stt = build_stt(cfg.providers, ledger, interrupt=stt_interrupts, forced=forced, voice=voice)
            front = [transport.input(), InputVoiceTap(log.emit, adaptive=True), mic_gate, _vad(cfg), stt,
                     gate, *echo_procs, *address_procs, *duck_procs]  # fmt: skip
            back = [*filler_procs, clips, transport.output()]
        elif input_mode == "wav":
            source = ScriptedMicSource(segments or [Segment(wav)], on_event=log.emit)
            stt = build_stt(cfg.providers, ledger, script=stt_script or [say], interrupt=stt_interrupts,
                            forced=forced, voice=voice)  # fmt: skip
            front = [source, InputVoiceTap(log.emit), mic_gate, _vad(cfg), stt, gate, *echo_procs, *address_procs,
                     *duck_procs]  # fmt: skip
            output = PacedNullOutputTransport(
                audio["out_sample_rate"], on_event=log.emit, audio_out_filter=duck_gain
            )
            OutputVoiceTap(log.emit, device_latency_s=0.0).attach(output)
            back = [*filler_procs, clips, output]
        elif input_mode == "text":
            front, back = [], [clips]  # no output device: the clip is logged and goes nowhere
        else:
            raise ValueError(f"unknown input mode {input_mode!r}")

        if force_end and input_mode != "text":
            force_end.stt = stt
        pipeline = Pipeline(
            [*front, aggregators.user(), llm, PreambleFilter(log.emit), tts, *tap_procs, *back, aggregators.assistant()]
        )
        worker = PipelineWorker(
            pipeline,
            params=PipelineParams(
                audio_in_sample_rate=audio["in_sample_rate"],
                audio_out_sample_rate=audio["out_sample_rate"],
                enable_metrics=True,
                enable_usage_metrics=True,
            ),
            observers=[turn_obs, cost_obs, *([network] if network else [])],
            idle_timeout_secs=None,
        )

        async def speak(text: str, *, append_to_context: bool = True) -> None:
            await worker.queue_frame(TTSSpeakFrame(text, append_to_context=append_to_context))
            turn_obs.reply_done.set()

        async def report(note: str) -> None:
            await worker.queue_frame(
                LLMMessagesAppendFrame(messages=[{"role": "user", "content": note}], run_llm=True)
            )

        policy.speak = speak
        policy.report = report
        worker.add_observer(
            LLMErrorRecovery(cfg.profile["recovery"]["llm_error_phrase"], speak, lambda: log.turn_id, log.emit,
                             network=network)  # fmt: skip
        )

        async def text_turn(text: str) -> None:
            log.next_turn()
            log.emit("user_text", text=text)
            turn_obs.reply_done.clear()
            if verdict := policy.on_transcript(text):
                await speak(verdict.phrase)
                if len(text.split()) <= policy.answer_max_words:
                    return
                turn_obs.reply_done.clear()
            if addressing and not addressing.admit(text):
                return
            nonlocal stayed_silent
            stayed_silent = False
            await worker.queue_frame(
                LLMMessagesAppendFrame(messages=[{"role": "user", "content": text}], run_llm=True)
            )
            await asyncio.wait_for(turn_obs.reply_done.wait(), reply_timeout_s)
            if not stayed_silent:
                await _assistant_committed(context)

        async def finish_after_reply() -> None:
            try:
                if input_mode == "text":
                    # A number in the script is a pause, to outlast an undo window.
                    for turn in turns or [say]:
                        if isinstance(turn, str):
                            await text_turn(turn)
                        else:
                            await asyncio.sleep(turn)
                if source is not None:
                    # Answer the last scripted utterance, not an earlier one.
                    await asyncio.wait_for(source.all_injected.wait(), reply_timeout_s)
                    turn_obs.reply_done.clear()
                    await asyncio.wait_for(turn_obs.reply_done.wait(), last_reply_timeout_s or reply_timeout_s)
                await policy.settled()
            except TimeoutError:
                log.emit("reply_timeout")
                logger.error(f"no reply within {reply_timeout_s}s")
            await worker.stop_when_done()

        if input_mode == "mic":
            # The mute switch: `kill -USR1 <pid>` now, a button on the Pi later.
            def toggle_mute() -> None:
                muted = mic_gate.toggle_mute()
                logger.warning("MIC MUTED" if muted else "mic live")

            asyncio.get_running_loop().add_signal_handler(signal.SIGUSR1, toggle_mute)
            if ui:
                ui.on_mute = toggle_mute

        if ui:
            async def screen_tool(tool: str, args: dict) -> dict:
                result = await policy.run_from_screen(tool, args)
                log.emit("ui_action", tool=tool, args=args, result=result)
                return result

            async def switch_agent() -> None:
                log.emit("agent_switch_requested")
                await worker.cancel(reason="agent switch")

            ui.on_screen_tool = screen_tool
            ui.on_switch = switch_agent
            ui.state.screen_tools = policy.screen_tools()

        runner = WorkerRunner(handle_sigint=handle_sigint)
        await runner.add_workers(worker)
        finisher = None
        if input_mode != "mic":
            finisher = asyncio.create_task(finish_after_reply())
        clock_file = (Path(data_dir) if data_dir else cfg.path("data_dir")) / "clock.json"
        timers = asyncio.create_task(TimerWatch(clock_file, speak, log.emit).run())
        quiet = lambda: turn_obs.quiet and not policy.busy()  # noqa: E731
        delegated = asyncio.create_task(DelegateWatch(clock_file.with_name("delegate.json"), speak, quiet, log.emit).run())
        stats = asyncio.create_task(SystemStats(log.emit).run()) if input_mode == "mic" else None
        lag = asyncio.create_task(LoopLag(log.emit).run()) if input_mode == "mic" else None
        try:
            await runner.run()
        finally:
            shutdown("pipeline")
            for task in filter(None, (finisher, timers, delegated, stats, lag)):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await policy.close()
            shutdown("tool_policy")
            if network:
                await network.close()
            if ui:
                ui.on_screen_tool = ui.on_switch = ui.on_mute = None
                ui.state.screen_tools = []
                with contextlib.suppress(ValueError):
                    log.listeners.remove(ui.state.on_event)
            if owns_ui:
                await ui.stop()
                shutdown("ui")

    shutdown("mcp_clients")
    await cost_obs.flush_tts()
    log.emit("run_end", session_usd=round(cost_obs.session_usd, 6))
    log.close()
    return BotResult(
        run_id=log.run_id,
        events_path=log.path,
        transcripts=turn_obs.transcripts,
        llm_text="".join(turn_obs.llm_text).strip(),
        spoken_text=" ".join(turn_obs.spoken_text).strip(),
        tool_calls=turn_obs.tool_calls,
        session_usd=cost_obs.session_usd,
        policy_decisions=policy.decisions,
        context_messages=list(context.get_messages()),
        output=output,
    )


def _spotify_player(ui: UiServer, log: EventLog | None = None) -> None:
    ui.state.spotify_player = True
    ui.spotify_token = SpotifyBackend().access_token
    if log is not None:
        ui.on_player_status = lambda state, detail: log.emit("spotify_player", state=state, detail=detail)


async def run_agents(cfg: Config, **kwargs) -> BotResult:
    """Live sessions behind one robot face, one agent at a time (ADR-039). Picking another agent
    on the face ends the session; this starts the next with the new agent. Ctrl-C ends it all."""
    ui_cfg = cfg.profile["ui"]
    data = cfg.path("data_dir")
    store = AgentStore(data, cfg.providers)
    ui = UiServer(UiState(data), ui_cfg["host"], ui_cfg["port"])
    ui.agents = store
    if ui_cfg["spotify_player"]:
        _spotify_player(ui)
    await ui.start()
    logger.info(f"robot face at http://{ui_cfg['host']}:{ui_cfg['port']}")
    result = None
    try:
        while True:
            agent = store.active()
            if result is not None:
                ui.state.set_agent(agent, announce=True)
            logger.info(f"agent: {agent.name} ({agent.model})")
            result = await run_bot(session_config(cfg, agent), agent=agent, ui=ui, **kwargs)
            if not ui.state.switching:
                return result
    finally:
        await ui.stop()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", choices=["mac", "pi"], default="mac")
    ap.add_argument("--input", choices=["mic", "wav", "text"], default="mic")
    ap.add_argument("--mcp", choices=["stdio", "http"], default="stdio")
    ap.add_argument("--say", default="set a timer for one minute",
                    help="text mode: the request; wav mode with fake STT: what the fake 'hears'")  # fmt: skip
    ap.add_argument("--wav", type=Path)
    ap.add_argument("--stages", default="",
                    help="override providers.yaml stages for this run, e.g. stt=deepgram_flux,llm=groq")  # fmt: skip
    ap.add_argument("--flux", default="",
                    help="override Flux settings for this run, e.g. eot_threshold=0.6,eot_timeout_ms=2000")  # fmt: skip
    ap.add_argument("--turn-stop", choices=["auto", "smart_turn", "flux", "flux_forced", "speech_timeout"],
                    help="override the profile's turn.stop")  # fmt: skip
    ap.add_argument("--barge-in", choices=["immediate", "duck"], help="override barge_in.mode")
    ap.add_argument("--reply-templates", action="store_true", help="skip the second LLM call (ADR-020)")
    ap.add_argument("--echo-cancel", action="store_true", help="WebRTC AEC, for live runs on speakers")
    ap.add_argument("--ui", action="store_true", help="serve the robot face (harness/ui.py, ADR-027)")
    ap.add_argument("--spotify-player", action="store_true", help="play Spotify in the face page (ADR-031)")
    ap.add_argument("--servers", default="", help=f"task servers, default all: {','.join(TASK_SERVERS)}")
    ap.add_argument("--agent", default="", help="agent id from configs/agents.yaml or the face's builder (ADR-039)")
    ap.add_argument("--model", default="", help="a model from providers.openrouter.models, or direct, for this "
                    "run's agent instead of its own; live runs only, a fake LLM stays fake (ADR-040)")
    args = ap.parse_args()
    if args.input == "wav" and not args.wav:
        ap.error("--input wav needs --wav")

    cfg = load_config(args.profile)
    for item in filter(None, args.stages.split(",")):
        stage, _, choice = item.partition("=")
        if stage not in cfg.providers["stages"]:
            ap.error(f"unknown stage {stage!r}")
        cfg.providers["stages"][stage] = choice
    if args.spotify_player:
        cfg.profile["ui"]["spotify_player"] = True
    if cfg.profile["ui"]["spotify_player"]:
        cfg.profile["ui"]["enabled"] = True
        cfg.providers["stages"]["music"] = "spotify"
        os.environ["ME3_SPOTIFY_DEVICE"] = PLAYER_NAME
    os.environ["ME3_MUSIC_BACKEND"] = cfg.providers["stages"].get("music", "fake")
    flux = cfg.providers["providers"]["deepgram"]["flux"]
    for item in filter(None, args.flux.split(",")):
        name, _, value = item.partition("=")
        if name not in flux:
            ap.error(f"unknown Flux setting {name!r}")
        flux[name] = int(value) if name == "eot_timeout_ms" else float(value)
    if args.turn_stop:
        cfg.profile["turn"]["stop"] = args.turn_stop
    if args.barge_in:
        cfg.profile["barge_in"]["mode"] = args.barge_in
    if args.reply_templates:
        cfg.tool_policy["reply_templates"]["enabled"] = True
    if args.echo_cancel:
        cfg.profile["audio"]["echo_cancel"] = True

    if args.ui:
        cfg.profile["ui"]["enabled"] = True

    names = list(filter(None, args.servers.split(",")))
    if unknown := set(names) - set(TASK_SERVERS):
        ap.error(f"unknown servers {sorted(unknown)}")
    servers = {n: TASK_SERVERS[n] for n in names} or None

    data = cfg.path("data_dir")
    if copied := seed_documents(data / "documents"):
        print(f"copied {len(copied)} sample documents into {data / 'documents'}")
    store = AgentStore(data, cfg.providers)
    face_picks = args.input == "mic" and cfg.profile["ui"]["enabled"]
    try:
        # A scripted run uses --agent or the default, never the face's last pick.
        agent = store.get(args.agent) if args.agent else store.builtins()[store.default_id]
        agent = store.with_model(agent, args.model or None)
        if face_picks and (args.agent or args.model):
            store.set_active(agent.id, args.model or None)
    except ValueError as e:
        ap.error(str(e))
    common = dict(input_mode=args.input, mcp_transport=args.mcp, say=args.say, wav=args.wav, servers=servers)
    try:
        if face_picks:
            result = asyncio.run(run_agents(cfg, **common))
        else:
            result = asyncio.run(run_bot(session_config(cfg, agent), agent=agent, **common))
    except EchoRisk as e:
        raise SystemExit(f"refused: {e}") from None
    print(f"run {result.run_id}")
    print(f"  transcripts: {result.transcripts}")
    print(f"  tool calls:  {result.tool_calls}")
    print(f"  llm text:    {result.llm_text!r}")
    print(f"  spoken:      {result.spoken_text!r}")
    print(f"  cost:        ${result.session_usd:.4f}")
    print(f"  events:      {result.events_path}")


if __name__ == "__main__":
    main()
