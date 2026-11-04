"""Repeat runner for the latency probe (ADR-036): N scripted wav turns per session, then the report.

Each session is one ``run_bot`` in wav mode. The turns cycle through a fixed list of requests,
half that call a tool and half that do not, spoken by macOS ``say`` (synthesized, never a
recording of a person) and cached under data/latency_bench/. A turn starts once the bot has
replied and been quiet for ``--gap`` seconds, so no turn barges in on the one before.

    uv run python -m bench.latency_bench                           # 3 sessions x 8 turns, fakes
    uv run python -m bench.latency_bench --sessions 5 --turns 10
    ME3_LIVE=1 uv run python -m bench.latency_bench --stages stt=deepgram_flux,llm=anthropic,tts=cartesia

Fakes cost nothing. ``--stages`` naming any real provider refuses without ME3_LIVE=1, and every
paid call still passes the cost ledger's daily caps.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from loguru import logger

from bench.latency_probe import WARMUP_TURNS, build_report, format_table, write_report
from bench.run_report import is_live
from harness.bot import run_bot
from harness.config import REPO_ROOT, Config, load_config
from harness.file_input import Segment
from harness.mcp_tools import TASK_SERVERS

BENCH_DIR = REPO_ROOT / "data" / "latency_bench"

# (request, calls a tool with the fake LLM and the clock server)
REQUESTS = [
    ("set a timer for ten minutes", True),
    ("hello there how are you today", False),
    ("what time is it", True),
    ("tell me something nice", False),
    ("what timers do I have", True),
    ("thanks that is all for now", False),
]


def apply_stages(cfg: Config, spec: str) -> dict[str, str]:
    """Override providers.yaml stages; a real provider needs ME3_LIVE=1."""
    stages = cfg.providers["stages"]
    for item in filter(None, spec.split(",")):
        stage, _, choice = item.partition("=")
        if stage not in stages:
            raise SystemExit(f"unknown stage {stage!r}")
        stages[stage] = choice
    if is_live(stages) and os.environ.get("ME3_LIVE") != "1":
        live = ",".join(f"{k}={v}" for k, v in stages.items() if not str(v).startswith("fake"))
        raise SystemExit(f"refused: {live} would spend money. Set ME3_LIVE=1 to run live stages.")
    return stages


def request_wav(text: str, out_dir: Path = BENCH_DIR / "wavs") -> Path:
    """16 kHz mono speech for ``text`` from macOS ``say``, cached."""
    if not shutil.which("say"):
        raise SystemExit("needs macOS `say` to make the request audio")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / (re.sub(r"\W+", "_", text).strip("_") + ".wav")
    if not path.exists():
        subprocess.run(["say", "-o", str(path), "--data-format=LEI16@16000", text + "."], check=True)
    return path


def script(turns: int, offset: int = 0) -> list[str]:
    return [REQUESTS[(offset + i) % len(REQUESTS)][0] for i in range(turns)]


async def run_session(cfg: Config, texts: list[str], *, gap_s: float, data_dir: Path,
                      servers: dict[str, str] | None) -> Path:  # fmt: skip
    segments = [Segment(request_wav(texts[0]), delay_s=1.0)]
    segments += [Segment(request_wav(t), after="bot_quiet", delay_s=gap_s) for t in texts[1:]]
    result = await run_bot(
        cfg, input_mode="wav", segments=segments, stt_script=texts, data_dir=str(data_dir),
        reply_timeout_s=60.0 * len(texts), last_reply_timeout_s=60.0, handle_sigint=False,
        servers=servers,
    )  # fmt: skip
    return result.events_path


async def run_bench(cfg: Config, *, sessions: int, turns: int, gap_s: float = 1.0,
                    servers: dict[str, str] | None = None, data_dir: Path | None = None) -> list[Path]:  # fmt: skip
    """Run the sessions one after another and return their event logs."""
    data_dir = data_dir or BENCH_DIR / "state"
    paths = []
    for s in range(sessions):
        # A fresh state folder per session, so timers from one never fire in the next.
        state = data_dir / f"session-{s}"
        shutil.rmtree(state, ignore_errors=True)
        paths.append(await run_session(cfg, script(turns, s), gap_s=gap_s, data_dir=state, servers=servers))
        print(f"session {s + 1}/{sessions}: {paths[-1].name}", flush=True)
    return paths


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", choices=["mac", "pi"], default="mac")
    ap.add_argument("--sessions", type=int, default=3)
    ap.add_argument("--turns", type=int, default=8, help="turns per session, warm-up included")
    ap.add_argument("--warmup", type=int, default=WARMUP_TURNS)
    ap.add_argument("--gap", type=float, default=1.0, help="seconds of bot quiet before the next turn")
    ap.add_argument("--stages", default="", help="e.g. stt=deepgram_flux,llm=anthropic (needs ME3_LIVE=1)")
    ap.add_argument("--all-servers", action="store_true", help="start every task server, not just clock")
    ap.add_argument("--verbose", action="store_true", help="keep the pipeline's debug log")
    args = ap.parse_args(argv)
    if not args.verbose:
        logger.remove()  # pipeline logs would bury the report
        logger.add(sys.stderr, level="WARNING")
    if args.turns <= args.warmup:
        ap.error(f"--turns must exceed --warmup ({args.warmup}) to measure anything")

    cfg = load_config(args.profile)
    apply_stages(cfg, args.stages)
    servers = None if args.all_servers else {"clock": TASK_SERVERS["clock"]}
    paths = asyncio.run(run_bench(cfg, sessions=args.sessions, turns=args.turns, gap_s=args.gap, servers=servers))
    report = build_report(paths, warmup=args.warmup)
    print(format_table(report))
    print(f"report: {write_report(report)}")


if __name__ == "__main__":
    main()
