"""Score class-benchmark runs with the LLM judge, inside our cost ledger (ADR-044).

fd-benchmark calls the judge itself, so its spend would bypass ``bench/cost_ledger.py``. This
wrapper checks the OpenRouter cap first, reads the key's usage from OpenRouter's key endpoint
before and after (a free account call, not a model call), runs ``fdbench score``, and records the
difference in the ledger. The key reaches fdbench through the environment, never the command line.

    uv run python -m bench.fdb_judge fd-benchmark/runs/quielq/<run> [<run> ...]

The default judge is GPT-4o through OpenRouter, the model the Full-Duplex-Bench papers used.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

from bench.cost_ledger import CostLedger, SpendRefused
from harness.config import REPO_ROOT, load_config

FDB = REPO_ROOT / "fd-benchmark"
KEY_URL = "https://openrouter.ai/api/v1/key"


def key_usage(key: str) -> float:
    """Credits this key has used so far, in USD, from OpenRouter's key endpoint."""
    r = httpx.get(KEY_URL, headers={"Authorization": f"Bearer {key}"}, timeout=20)
    r.raise_for_status()
    return float(r.json()["data"]["usage"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", type=Path)
    ap.add_argument("--model", default="openai/gpt-4o")
    ap.add_argument("--settle-s", type=float, default=15.0, help="wait for OpenRouter's usage to catch up")
    args = ap.parse_args(argv)

    cfg = load_config("mac")  # also loads .env
    ledger = CostLedger.from_providers_config(cfg.providers, cfg.path("data_dir") / "cost_ledger.jsonl")
    try:
        ledger.authorize("openrouter")
    except SpendRefused as e:
        print(f"refused: {e}")
        return 1
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        print("refused: OPENROUTER_API_KEY is not set in .env")
        return 1

    before = key_usage(key)
    env = os.environ | {"OPENROUTER_API_KEY": key}
    failed = 0
    for run in args.runs:
        cmd = [str(FDB / ".venv/bin/python"), "-m", "fdbench", "score", str(run.resolve()),
               "--judge-provider", "openrouter", "--judge-model", args.model]  # fmt: skip
        print(f"judging {run} with {args.model}")
        failed += subprocess.run(cmd, cwd=FDB, env=env).returncode != 0
    time.sleep(args.settle_s)
    spent = max(0.0, key_usage(key) - before)
    ledger.record("openrouter", f"fdbench_judge:{args.model}", len(args.runs), spent)
    print(f"judge spend ${spent:.4f} recorded under openrouter")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
