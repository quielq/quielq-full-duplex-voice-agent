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
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import LLMMessagesAppendFrame
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
from harness.audio_io import EchoRisk, local_transport
from harness.config import Config, load_config
from harness.costs import CostObserver
from harness.ducking import DuckGain, DuckingProcessor
from harness.events import EventLog, TurnEventObserver
from harness.file_input import ScriptedMicSource, Segment
from harness.mcp_tools import clock_client
from harness.null_output import PacedNullOutputTransport
from harness.providers import build_llm, build_stt, build_tts

SYSTEM_PROMPT = (
    "You are a hands-free voice assistant. Replies are spoken aloud, so keep them to one or "
    "two short sentences with no lists or formatting. Use the tools for timers and alarms."
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
    context_messages: list[dict] = field(default_factory=list)
    output: PacedNullOutputTransport | None = None


def _turn_strategies(cfg: Config) -> UserTurnStrategies | None:
    """Turn detection from profile ``turn.stop``: auto | smart_turn | flux | speech_timeout.

    "auto" is flux when the STT is Deepgram Flux and smart_turn otherwise. Flux has no
    finalize message, so Smart Turn cannot end a Flux turn early, and mixing the two
    stalled a turn in testing (ADR-005).

    Pipecat 1.x API: interruptions and turn ends come from user_turn_strategies.
    Never allow_interruptions / interruption_strategies / TransportParams(vad_analyzer=...).
    "flux" returns None so the aggregator applies the strategies Deepgram Flux
    recommends (its own start and end of turn); it needs stt=deepgram_flux.
    """
    mode = cfg.profile["turn"]["stop"]
    flux_stt = cfg.providers["stages"]["stt"] == "deepgram_flux"
    if mode == "auto":
        mode = "flux" if flux_stt else "smart_turn"
    elif mode != "flux" and flux_stt:
        raise ValueError(f"turn.stop={mode} does not work with Deepgram Flux STT; use auto or flux")
    if mode == "flux":
        if cfg.providers["stages"]["stt"] != "deepgram_flux":
            raise ValueError("turn.stop=flux needs stages.stt=deepgram_flux")
        return None
    if mode == "smart_turn":
        stop = [TurnAnalyzerUserTurnStopStrategy(turn_analyzer=LocalSmartTurnAnalyzerV3())]
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


async def run_bot(
    cfg: Config,
    *,
    input_mode: str = "mic",
    mcp_transport: str = "stdio",
    say: str = "set a timer for one minute",
    wav: Path | None = None,
    segments: list[Segment] | None = None,
    stt_script: list[str] | None = None,
    data_dir: str | None = None,
    reply_timeout_s: float = 60.0,
    handle_sigint: bool = True,
) -> BotResult:
    log = EventLog(cfg.path("logs_dir"))
    ledger = CostLedger.from_providers_config(
        cfg.providers, cfg.path("data_dir") / "cost_ledger.jsonl"
    )
    turn_obs = TurnEventObserver(log)
    worker: PipelineWorker | None = None

    async def on_cap_reached(provider: str) -> None:
        log.emit("spend_cap_reached", provider=provider)
        logger.warning(f"{provider}: daily spend cap reached, stopping the agent")
        if worker is not None:
            await worker.stop_when_done()

    cost_obs = CostObserver(ledger, cfg.providers, log.run_id, on_cap_reached)
    log.emit("run_start", profile=cfg.name, input=input_mode, mcp=mcp_transport,
             stages=cfg.providers["stages"])  # fmt: skip

    async with clock_client(mcp_transport, data_dir) as mcp:
        # The system prompt lives on the LLM service (system messages in LLMContext are
        # deprecated since Pipecat 1.9).
        context = LLMContext(tools=await mcp.tools())
        aggregators = LLMContextAggregatorPair(
            context, user_params=LLMUserAggregatorParams(user_turn_strategies=_turn_strategies(cfg))
        )
        llm = build_llm(cfg.providers, ledger, system_instruction=SYSTEM_PROMPT)
        tts = build_tts(cfg.providers, ledger)

        audio = cfg.profile["audio"]
        source = output = None
        duck_gain, duck_procs = _ducking(cfg, log)
        stt_interrupts = duck_gain is None
        if input_mode == "mic":
            transport = local_transport(audio, audio_out_filter=duck_gain)
            stt = build_stt(cfg.providers, ledger, interrupt=stt_interrupts)
            front = [transport.input(), _vad(cfg), stt, *duck_procs]
            back = [transport.output()]
        elif input_mode == "wav":
            source = ScriptedMicSource(segments or [Segment(wav)], on_event=log.emit)
            stt = build_stt(cfg.providers, ledger, script=stt_script or [say], interrupt=stt_interrupts)
            front = [source, _vad(cfg), stt, *duck_procs]
            output = PacedNullOutputTransport(
                audio["out_sample_rate"], on_event=log.emit, audio_out_filter=duck_gain
            )
            back = [output]
        elif input_mode == "text":
            front, back = [], []
        else:
            raise ValueError(f"unknown input mode {input_mode!r}")

        pipeline = Pipeline(
            [*front, aggregators.user(), llm, tts, *back, aggregators.assistant()]
        )
        worker = PipelineWorker(
            pipeline,
            params=PipelineParams(
                audio_in_sample_rate=audio["in_sample_rate"],
                audio_out_sample_rate=audio["out_sample_rate"],
                enable_metrics=True,
                enable_usage_metrics=True,
            ),
            observers=[turn_obs, cost_obs],
            idle_timeout_secs=None,
        )

        async def finish_after_reply() -> None:
            if input_mode == "text":
                log.next_turn()
                log.emit("user_text", text=say)
                await worker.queue_frame(
                    LLMMessagesAppendFrame(messages=[{"role": "user", "content": say}], run_llm=True)
                )
            try:
                if source is not None:
                    # Answer the last scripted utterance, not an earlier one.
                    await asyncio.wait_for(source.all_injected.wait(), reply_timeout_s)
                    turn_obs.reply_done.clear()
                await asyncio.wait_for(turn_obs.reply_done.wait(), reply_timeout_s)
            except TimeoutError:
                log.emit("reply_timeout")
                logger.error(f"no reply within {reply_timeout_s}s")
            await worker.stop_when_done()

        runner = WorkerRunner(handle_sigint=handle_sigint)
        await runner.add_workers(worker)
        finisher = None
        if input_mode != "mic":
            finisher = asyncio.create_task(finish_after_reply())
        try:
            await runner.run()
        finally:
            if finisher:
                finisher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await finisher

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
        context_messages=list(context.get_messages()),
        output=output,
    )


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
    ap.add_argument("--turn-stop", choices=["auto", "smart_turn", "flux", "speech_timeout"],
                    help="override the profile's turn.stop")  # fmt: skip
    ap.add_argument("--barge-in", choices=["immediate", "duck"], help="override barge_in.mode")
    args = ap.parse_args()
    if args.input == "wav" and not args.wav:
        ap.error("--input wav needs --wav")

    cfg = load_config(args.profile)
    for item in filter(None, args.stages.split(",")):
        stage, _, choice = item.partition("=")
        if stage not in cfg.providers["stages"]:
            ap.error(f"unknown stage {stage!r}")
        cfg.providers["stages"][stage] = choice
    if args.turn_stop:
        cfg.profile["turn"]["stop"] = args.turn_stop
    if args.barge_in:
        cfg.profile["barge_in"]["mode"] = args.barge_in

    try:
        result = asyncio.run(
            run_bot(cfg, input_mode=args.input, mcp_transport=args.mcp,
                    say=args.say, wav=args.wav)  # fmt: skip
        )
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
