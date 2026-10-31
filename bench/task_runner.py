"""Layered task tests (ADR-009): next reply, tool call, and scripted conversation.

Cases live in bench/task_suite/cases/*.yaml and run through ``run_bot`` text mode, so only
the LLM varies. Gold answers are checked in plain code, so the suite adds no evaluator spend.
Audio timing stays in bench/interruption_test.py for now.

    uv run python -m bench.task_runner                               # all cases, fake LLM
    uv run python -m bench.task_runner --repeat 5 --layer tool_call
    ME3_LIVE=1 uv run python -m bench.task_runner --stages llm=groq  # spends money

A case with a repeat count reports a pass rate, not a single pass or fail. Each failure gets
exactly one bucket from BUCKETS, and spend goes through the cost ledger as in any run.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import re
import sqlite3
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from harness.bot import run_bot
from harness.config import Config, load_config
from harness.mcp_tools import TASK_SERVERS

CASES_DIR = Path(__file__).parent / "task_suite" / "cases"
LAYERS = ("next_reply", "tool_call", "conversation")
BUCKETS = ("wrong_tool", "missed_tool", "bad_args", "wrong_state", "spoke_when_silent",
           "timeout", "provider_error")  # fmt: skip

DEFAULT_UNDO_S = 0.2  # keeps fake runs fast; a case that must beat the window sets its own
DEFAULT_REPLY_TIMEOUT_S = 30.0
SILENT_REPLY_TIMEOUT_S = 3.0


@dataclass
class Case:
    id: str
    layer: str
    server: str
    turns: list[str | float]
    expect: dict[str, Any]
    seed: dict[str, list[dict]] = field(default_factory=dict)
    undo_s: float | None = None


@dataclass
class Observed:
    """What one run did. ``classify`` needs nothing else, so it is testable without a bot."""

    tool_calls: list[dict] = field(default_factory=list)
    spoken: str = ""
    state: dict[str, list[dict]] = field(default_factory=dict)
    timed_out: bool = False
    provider_error: str = ""


@dataclass
class RunResult:
    case_id: str
    layer: str
    bucket: str | None
    detail: str
    usd: float
    seconds: float

    @property
    def passed(self) -> bool:
        return self.bucket is None


def load_cases(paths: list[Path] | None = None) -> list[Case]:
    cases = []
    ids: set[str] = set()
    for path in paths or sorted(CASES_DIR.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text())
        for raw in doc["cases"]:
            if raw["layer"] not in LAYERS:
                raise ValueError(f"{path.name}: {raw['id']}: unknown layer {raw['layer']!r}")
            if raw["id"] in ids:
                raise ValueError(f"{path.name}: duplicate case id {raw['id']!r}")
            ids.add(raw["id"])
            cases.append(Case(server=doc["server"], **raw))
    return cases


def _value_matches(want: Any, got: Any) -> bool:
    if isinstance(want, dict) and set(want) == {"re"}:
        return re.search(want["re"], str(got)) is not None
    return want == got


def _args_match(want: dict, got: dict) -> bool:
    return all(k in got and _value_matches(v, got[k]) for k, v in want.items())


def _state_problem(want: dict, have: dict[str, list[dict]]) -> str:
    for server, spec in want.items():
        items = have.get(server, [])
        if "count" in spec and len(items) != spec["count"]:
            return f"{server}: {len(items)} items, wanted {spec['count']}"
        for wanted in spec.get("has", []):
            if not any(_args_match(wanted, item) for item in items):
                return f"{server}: nothing matches {wanted}"
    return ""


def classify(expect: dict, seen: Observed) -> tuple[str | None, str]:
    """Return (bucket, detail); (None, "") is a pass. Earlier checks explain later ones, so a
    wrong tool is reported as that and not also as the wrong state it caused."""
    if seen.provider_error:
        return "provider_error", seen.provider_error
    silent = expect.get("silent", False)
    if silent and seen.spoken:
        return "spoke_when_silent", seen.spoken[:80]
    if seen.timed_out and not silent:
        return "timeout", "no reply in time"

    names = [c["name"] for c in seen.tool_calls]
    if "tools" in expect and names != expect["tools"]:
        bucket = "missed_tool" if expect["tools"] and not names else "wrong_tool"
        return bucket, f"called {names}, wanted {expect['tools']}"
    missing = [t for t in expect.get("tools_include", []) if t not in names]
    if missing:
        return ("wrong_tool" if names else "missed_tool"), f"called {names}, missing {missing}"

    for tool, want in expect.get("args", {}).items():
        calls = [c for c in seen.tool_calls if c["name"] == tool]
        if not any(_args_match(want, c["args"]) for c in calls):
            return "bad_args", f"{tool}: {[c['args'] for c in calls]}, wanted {want}"

    if problem := _state_problem(expect.get("state", {}), seen.state):
        return "wrong_state", problem
    pattern = expect.get("spoken_re")
    if pattern and not re.search(pattern, seen.spoken, re.I):
        # The reply is wrong though the action was right. The fixed buckets have no reply
        # bucket, and a spoken claim about state is the nearest thing.
        return "wrong_state", f"spoken {seen.spoken[:80]!r} does not match {pattern!r}"
    return None, ""


def seed_clock(data_dir: Path, items: list[dict]) -> None:
    store = {}
    for item in items:
        fires_at = time.time() + item["in_s"]
        store[item["id"]] = {k: v for k, v in item.items() if k != "in_s"} | {"fires_at": fires_at}
    (data_dir / "clock.json").write_text(json.dumps(store))


def read_clock(data_dir: Path) -> list[dict]:
    path = data_dir / "clock.json"
    return list(json.loads(path.read_text()).values()) if path.exists() else []


def seed_notes(data_dir: Path, items: list[dict]) -> None:
    with sqlite3.connect(data_dir / "notes.db") as db:
        db.execute("CREATE TABLE IF NOT EXISTS notes (id TEXT PRIMARY KEY, text TEXT, created_at REAL)")
        db.executemany("INSERT INTO notes VALUES (:id, :text, 0)", items)


def read_notes(data_dir: Path) -> list[dict]:
    path = data_dir / "notes.db"
    if not path.exists():
        return []
    with sqlite3.connect(path) as db:
        return [{"id": i, "text": t} for i, t in db.execute("SELECT id, text FROM notes")]


def seed_thermostat(data_dir: Path, items: list[dict]) -> None:
    (data_dir / "thermostat.json").write_text(json.dumps(items[0]))


def read_thermostat(data_dir: Path) -> list[dict]:
    path = data_dir / "thermostat.json"
    return [json.loads(path.read_text())] if path.exists() else []


def seed_comms(data_dir: Path, items: list[dict]) -> None:
    (data_dir / "outbox.json").write_text(json.dumps({i["id"]: i for i in items}))


def read_comms(data_dir: Path) -> list[dict]:
    path = data_dir / "outbox.json"
    return list(json.loads(path.read_text()).values()) if path.exists() else []


# Each server's state, in the plain form cases compare against.
STATE: dict[str, Any] = {
    "clock": (seed_clock, read_clock),
    "notes": (seed_notes, read_notes),
    "thermostat": (seed_thermostat, read_thermostat),
    "comms": (seed_comms, read_comms),
}


def _pipeline_error(events_path: Path) -> str:
    for line in events_path.read_text().splitlines():
        event = json.loads(line)
        if event["event"] in ("pipeline_error", "spend_cap_reached"):
            return event.get("error") or event["event"]
    return ""


def _timed_out(events_path: Path) -> bool:
    return any(json.loads(line)["event"] == "reply_timeout" for line in events_path.read_text().splitlines())


async def run_case(
    base: Config, case: Case, *, transport: str = "stdio", all_servers: bool = False
) -> RunResult:
    cfg = copy.deepcopy(base)
    undo_s = DEFAULT_UNDO_S if case.undo_s is None else case.undo_s
    cfg.tool_policy["defaults"]["undo_s"] = undo_s
    for rule in (cfg.tool_policy["tools"] or {}).values():
        if "undo_s" in rule:
            rule["undo_s"] = undo_s
    silent = case.expect.get("silent", False)
    servers = TASK_SERVERS if all_servers else {case.server: TASK_SERVERS[case.server]}
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory() as d:
        data_dir = Path(d)
        for server, items in case.seed.items():
            STATE[server][0](data_dir, items)
        seen = Observed()
        usd = 0.0
        try:
            r = await run_bot(
                cfg,
                input_mode="text",
                mcp_transport=transport,
                turns=case.turns,
                data_dir=str(data_dir),
                handle_sigint=False,
                servers=servers,
                reply_timeout_s=SILENT_REPLY_TIMEOUT_S if silent else DEFAULT_REPLY_TIMEOUT_S,
            )
        except Exception as e:  # noqa: BLE001, a refused or failed provider is a result here
            seen.provider_error = f"{type(e).__name__}: {e}"
        else:
            usd = r.session_usd
            seen.tool_calls = r.tool_calls
            seen.spoken = r.spoken_text
            seen.timed_out = _timed_out(r.events_path)
            seen.provider_error = _pipeline_error(r.events_path)
            seen.state = {name: read(data_dir) for name, (_, read) in STATE.items()}
    bucket, detail = classify(case.expect, seen)
    return RunResult(case.id, case.layer, bucket, detail, usd, round(time.monotonic() - t0, 2))


def report(results: list[RunResult]) -> dict:
    """Pass rates per case and layer, failure buckets, and cost per successful task."""

    def rate(rows: list[RunResult]) -> dict:
        passed = sum(r.passed for r in rows)
        return {"passed": passed, "runs": len(rows), "pass_rate": round(passed / len(rows), 3)}

    by_case: dict[str, list[RunResult]] = {}
    by_layer: dict[str, list[RunResult]] = {}
    for r in results:
        by_case.setdefault(r.case_id, []).append(r)
        by_layer.setdefault(r.layer, []).append(r)
    successes = sum(r.passed for r in results)
    total_usd = sum(r.usd for r in results)
    return {
        "overall": rate(results),
        "layers": {k: rate(v) for k, v in by_layer.items()},
        "cases": {k: rate(v) for k, v in by_case.items()},
        "buckets": dict(Counter(r.bucket for r in results if r.bucket)),
        "failures": [{"case": r.case_id, "bucket": r.bucket, "detail": r.detail}
                     for r in results if r.bucket],  # fmt: skip
        "total_usd": round(total_usd, 6),
        "usd_per_success": round(total_usd / successes, 6) if successes else None,
    }


async def run_suite(
    cfg: Config,
    cases: list[Case],
    *,
    repeat: int = 1,
    transport: str = "stdio",
    all_servers: bool = False,
) -> dict:
    results = []
    for _ in range(repeat):
        for case in cases:
            results.append(await run_case(cfg, case, transport=transport, all_servers=all_servers))
    return report(results)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--layer", choices=LAYERS)
    ap.add_argument("--case", help="only case ids containing this text")
    ap.add_argument("--mcp", choices=["stdio", "http"], default="stdio")
    ap.add_argument("--stages", default="", help="only llm may change, e.g. llm=groq (needs ME3_LIVE=1)")
    ap.add_argument("--all-servers", action="store_true",
                    help="offer every server's tools, as the real agent does (slower start)")  # fmt: skip
    ap.add_argument("--out", type=Path, help="also write the report as JSON here")
    args = ap.parse_args()

    cfg = load_config("mac")
    cfg.providers["stages"] = {"stt": "fake", "llm": "fake", "tts": "fake"}
    for item in filter(None, args.stages.split(",")):
        stage, _, choice = item.partition("=")
        if stage != "llm":
            ap.error("these layers vary the LLM only")
        cfg.providers["stages"]["llm"] = choice
    if cfg.providers["stages"]["llm"] != "fake" and os.environ.get("ME3_LIVE") != "1":
        ap.error("a real LLM spends money: set ME3_LIVE=1 to confirm")

    cases = [
        c for c in load_cases()
        if (not args.layer or c.layer == args.layer) and (not args.case or args.case in c.id)
    ]  # fmt: skip
    if not cases:
        ap.error("no cases match")
    out = asyncio.run(run_suite(cfg, cases, repeat=args.repeat, transport=args.mcp,
                                all_servers=args.all_servers))  # fmt: skip
    text = json.dumps(out, indent=2)
    print(text)
    if args.out:
        args.out.write_text(text + "\n")


if __name__ == "__main__":
    main()
