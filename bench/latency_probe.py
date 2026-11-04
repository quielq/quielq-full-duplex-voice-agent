"""Latency probe: the project's standardized latency measurement (ADR-036).

Headline: end of the user's speech to the first reply audio, both from the samples. The start
is the last input sample over the threshold before end of turn (``user_audio_end``); the end is
the play time of the first TTS sample over the threshold at the output (``reply_audio_onset``).
Both come from harness/audio_marks.py. The thinking phrase (``local_audio_onset``) never counts
as reply audio. Pipecat events give the per-stage deltas, which are attribution only.

Statistics: p50, p90, and p95 (linear interpolation) over every turn of every run given, after
dropping the first three turns of each session as warm-up, for all turns, no-tool turns, and
tool turns. Cost per turn joins the cost ledger by run id.

    uv run python -m bench.latency_probe logs/events-<run_id>.jsonl [more logs or run ids]
    uv run python -m bench.latency_probe --last 5            # the five newest runs
    uv run python -m bench.latency_probe --from ids.txt      # one run id per line
    uv run python -m bench.latency_bench --sessions 3 --turns 8   # makes runs on fakes first

The table goes to stdout and the full report, every turn included, to logs/latency-<time>.json.
"""

from __future__ import annotations

import argparse
import bisect
import json
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from bench.run_report import LOGS_DIR, all_logs, find_log
from harness.config import REPO_ROOT

LEDGER_PATH = REPO_ROOT / "data" / "cost_ledger.jsonl"
WARMUP_TURNS = 3
GROUPS = ("all", "no_tool", "tool")

# Framework stage deltas, for attribution. (name, from_event, to_event); "last" picks the
# latest occurrence in the turn, "first" the earliest.
STAGES = [
    ("end_of_turn_after_speech", ("first", "injection_speech_end"), ("first", "end_of_turn")),
    ("vad_release", ("first", "injection_speech_end"), ("first", "vad_end")),
    ("tool_call", ("first", "tool_call_start"), ("first", "tool_call_end")),
    ("llm_first_token", ("last", "llm_request"), ("first", "llm_first_token")),
    ("tts_first_byte", ("first", "llm_first_token"), ("first", "tts_first_byte")),
    ("speech_end_to_tts_first_byte", ("first", "injection_speech_end"), ("first", "tts_first_byte")),
    ("speech_end_to_audio_out", ("first", "injection_speech_end"), ("first", "first_audio_out")),
    ("end_of_turn_to_first_audio_out", ("first", "end_of_turn"), ("first", "first_audio_out")),
    # Reported apart: the waiting phrase never counts toward the latency target (ADR-009).
    ("end_of_turn_to_filler", ("first", "end_of_turn"), ("first", "filler_audio_out")),
]


def load(path: Path) -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def _injection_turns(events: list[dict]) -> dict[int, int]:
    """Each injection event belongs to the turn of the first end of turn after it."""
    eots = sorted((e["t_ns"], e["turn_id"]) for e in events if e["event"] == "end_of_turn")
    times = [t for t, _ in eots]
    out = {}
    for i, e in enumerate(events):
        if e["event"].startswith("injection_"):
            k = bisect.bisect_left(times, e["t_ns"])
            if k < len(eots):
                out[i] = eots[k][1]
    return out


def stage_deltas(events: list[dict]) -> dict[int, dict[str, float]]:
    """Per turn, milliseconds between framework event pairs. Attribution only."""
    by_turn: dict[int, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    injection = _injection_turns(events)
    for i, e in enumerate(events):
        if e["event"].startswith("injection_"):
            if i in injection:
                by_turn[injection[i]][e["event"]].append(e["t_ns"])
            continue
        by_turn[e["turn_id"]][e["event"]].append(e["t_ns"])

    out: dict[int, dict[str, float]] = {}
    for turn, ev in sorted(by_turn.items()):
        row = {}
        for name, (fpick, fname), (tpick, tname) in STAGES:
            if ev.get(fname) and ev.get(tname):
                f = max(ev[fname]) if fpick == "last" else min(ev[fname])
                t = max(ev[tname]) if tpick == "last" else min(ev[tname])
                if t >= f:
                    row[name] = (t - f) / 1e6
        if row:
            out[turn] = row
    return out


def filler_rate(events: list[dict]) -> tuple[int, int]:
    """(turns with a waiting phrase, turns). A filler on most turns annoys, so this gets reviewed."""
    turns = {e["turn_id"] for e in events if e["event"] == "end_of_turn"}
    played = {e["turn_id"] for e in events if e["event"] == "filler_trigger"}
    return len(played & turns), len(turns)


@dataclass
class TurnLatency:
    run_id: str
    turn_id: int
    index: int  # 1 for the session's first user turn
    warmup: bool
    latency_ms: float | None  # the headline; None when unmeasured
    unmeasured: str | None = None  # why: no_speech_end, no_reply
    tools: list[str] = field(default_factory=list)
    filler: bool = False
    filler_ms: float | None = None  # speech end to the thinking phrase's first sample
    boundary: bool = False  # the reply's first chunk followed a clip chunk (within one chunk)
    stages: dict[str, float] = field(default_factory=dict)

    @property
    def group(self) -> str:
        return "tool" if self.tools else "no_tool"


def _first_in(events: list[dict], name: str, lo: int, hi: float, key: str = "t_ns") -> dict | None:
    hits = [e for e in events if e["event"] == name and lo < e[key] < hi]
    return min(hits, key=lambda e: e[key]) if hits else None


def measure_turns(events: list[dict], warmup: int = WARMUP_TURNS) -> list[TurnLatency]:
    """Every user turn in one session's log, measured from the audio marks where it can be."""
    run_id = events[0]["run_id"] if events else ""
    first_eot: dict[int, int] = {}
    for e in events:
        if e["event"] == "end_of_turn" and e["turn_id"] not in first_eot:
            first_eot[e["turn_id"]] = e["t_ns"]
    starts = sorted(e["t_ns"] for e in events if e["event"] == "user_turn_start")
    ends = sorted(e["audio_t_ns"] for e in events if e["event"] == "user_audio_end")
    deltas = stage_deltas(events)
    out = []
    prev_eot = 0
    for index, (turn_id, eot) in enumerate(sorted(first_eot.items(), key=lambda kv: kv[1]), 1):
        k = bisect.bisect_right(starts, eot)
        upper = starts[k] if k < len(starts) else float("inf")  # the next user turn
        row = TurnLatency(run_id, turn_id, index, index <= warmup, None, stages=dict(deltas.get(turn_id, {})))
        row.tools = [e["name"] for e in events if e["event"] == "tool_call_start" and eot < e["t_ns"] < upper]
        row.filler = any(e["event"] == "filler_trigger" and eot < e["t_ns"] < upper for e in events)
        # The user's speech ends at the last voiced sample before end of turn.
        j = bisect.bisect_right(ends, eot)
        speech_end = ends[j - 1] if j and ends[j - 1] > prev_eot else None
        reply = _first_in(events, "reply_audio_onset", eot, upper, "audio_t_ns")
        clip = _first_in(events, "local_audio_onset", eot, upper, "audio_t_ns")
        if speech_end is not None:
            row.stages["end_of_turn_wait"] = (eot - speech_end) / 1e6
        if reply is not None:
            row.stages["end_of_turn_to_reply_audio"] = (reply["audio_t_ns"] - eot) / 1e6
            row.boundary = bool(reply.get("boundary"))
        if speech_end is None:
            row.unmeasured = "no_speech_end"
        elif reply is None:
            row.unmeasured = "no_reply"
        else:
            row.latency_ms = (reply["audio_t_ns"] - speech_end) / 1e6
        if clip is not None and speech_end is not None and (reply is None or clip["audio_t_ns"] < reply["audio_t_ns"]):
            row.filler_ms = (clip["audio_t_ns"] - speech_end) / 1e6
        out.append(row)
        prev_eot = eot
    return out


def percentiles(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"n": 0, "p50": None, "p90": None, "p95": None, "mean": None, "min": None, "max": None}
    a = np.asarray(values, dtype=float)
    p50, p90, p95 = np.percentile(a, [50, 90, 95])
    return {"n": int(a.size), "p50": round(float(p50), 1), "p90": round(float(p90), 1),
            "p95": round(float(p95), 1), "mean": round(float(a.mean()), 1),
            "min": round(float(a.min()), 1), "max": round(float(a.max()), 1)}  # fmt: skip


def ledger_usd(path: Path, run_ids: set[str]) -> dict[str, float]:
    """Spend per run id from the cost ledger (bench/cost_ledger.py)."""
    usd: dict[str, float] = defaultdict(float)
    if path.exists():
        with open(path) as f:
            for line in f:
                e = json.loads(line)
                if e.get("run_id") in run_ids:
                    usd[e["run_id"]] += e["usd"]
    return dict(usd)


def _run_info(events: list[dict]) -> dict:
    start = next((e for e in events if e["event"] == "run_start"), {})
    end = next((e for e in events if e["event"] == "run_end"), {})
    return {"stages": start.get("stages", {}), "input": start.get("input"),
            "session_usd": end.get("session_usd")}  # fmt: skip


def build_report(paths: list[Path], *, warmup: int = WARMUP_TURNS, ledger: Path = LEDGER_PATH) -> dict:
    runs, turns = [], []
    for path in paths:
        events = load(path)
        if not events:
            continue
        rows = measure_turns(events, warmup)
        info = _run_info(events)
        runs.append({"run_id": events[0]["run_id"], "path": str(path), "turns": len(rows), **info})
        turns.extend(rows)

    spend = ledger_usd(ledger, {r["run_id"] for r in runs})
    for r in runs:
        if r["run_id"] in spend:
            r["usd"], r["usd_source"] = spend[r["run_id"]], "ledger"
        else:
            r["usd"], r["usd_source"] = r.get("session_usd") or 0.0, "run_end"
    total_turns = sum(r["turns"] for r in runs)
    total_usd = sum(r["usd"] for r in runs)

    kept = [t for t in turns if not t.warmup]
    measured = [t for t in kept if t.latency_ms is not None]
    headline = {"all": percentiles([t.latency_ms for t in measured])}
    for g in GROUPS[1:]:
        headline[g] = percentiles([t.latency_ms for t in measured if t.group == g])
    # Every kept turn, so a log from before the audio marks still shows its framework stages.
    stage_names = sorted({k for t in kept for k in t.stages})
    attribution = {s: percentiles([t.stages[s] for t in kept if s in t.stages]) for s in stage_names}
    fillers = [t for t in kept if t.filler]
    return {
        "method": "ADR-036: last voiced input sample before end of turn to first voiced TTS sample played",
        "warmup_turns": warmup,
        "runs": runs,
        "stage_sets": sorted({json.dumps(r["stages"], sort_keys=True) for r in runs}),
        "turns_total": len(turns),
        "turns_kept": len(kept),
        "turns_measured": len(measured),
        "unmeasured": dict(Counter(t.unmeasured for t in kept if t.unmeasured)),
        "boundary_turns": sum(t.boundary for t in measured),
        "headline_ms": headline,
        "thinking_phrase": {"turns": len(fillers), "of": len(kept),
                            "speech_end_to_clip_ms": percentiles([t.filler_ms for t in fillers if t.filler_ms is not None])},  # fmt: skip
        "attribution_ms": attribution,
        "cost": {"usd": round(total_usd, 6), "turns": total_turns,
                 "usd_per_turn": round(total_usd / total_turns, 6) if total_turns else None,
                 "sources": dict(Counter(r["usd_source"] for r in runs))},  # fmt: skip
        "turns": [asdict(t) | {"group": t.group} for t in turns],
    }


def _fmt(v) -> str:
    return "-" if v is None else f"{v:.0f}"


def format_table(report: dict) -> str:
    lines = []
    n_runs = len(report["runs"])
    lines.append(f"latency probe (ADR-036): {n_runs} runs, {report['turns_total']} turns, "
                 f"{report['turns_kept']} after dropping {report['warmup_turns']} warm-up turns per run, "
                 f"{report['turns_measured']} measured")  # fmt: skip
    for s in report["stage_sets"]:
        stages = json.loads(s)
        lines.append("  stages " + (",".join(f"{k}={v}" for k, v in stages.items()) or "unknown"))
    if len(report["stage_sets"]) > 1:
        lines.append("  WARNING: these runs mix stage sets; compare like with like")
    lines.append("")
    lines.append("end of user speech to first reply audio, from the samples (ms)")
    lines.append(f"  {'turns':<10}{'n':>5}{'p50':>8}{'p90':>8}{'p95':>8}{'mean':>8}{'min':>8}{'max':>8}")
    for g in GROUPS:
        p = report["headline_ms"][g]
        lines.append(f"  {g:<10}{p['n']:>5}" + "".join(f"{_fmt(p[k]):>8}" for k in ("p50", "p90", "p95", "mean", "min", "max")))
    if report["turns_measured"] and report["turns_measured"] < 20:
        lines.append("  (p95 needs about 20 turns or more to mean much)")
    tp = report["thinking_phrase"]
    clip = tp["speech_end_to_clip_ms"]
    lines.append(f"thinking phrase on {tp['turns']} of {tp['of']} turns"
                 + (f", speech end to clip p50 {_fmt(clip['p50'])} ms (not counted above)" if clip["n"] else ""))  # fmt: skip
    if report["boundary_turns"]:
        lines.append(f"{report['boundary_turns']} turns had reply audio in the chunk after a clip (within 40 ms)")
    if report["unmeasured"]:
        lines.append("unmeasured: " + ", ".join(f"{k} {v}" for k, v in sorted(report["unmeasured"].items())))
    lines.append("")
    lines.append("attribution, Pipecat events and audio marks (ms)")
    lines.append(f"  {'stage':<34}{'n':>5}{'p50':>8}{'p90':>8}")
    for name, p in report["attribution_ms"].items():
        lines.append(f"  {name:<34}{p['n']:>5}{_fmt(p['p50']):>8}{_fmt(p['p90']):>8}")
    c = report["cost"]
    per = "-" if c["usd_per_turn"] is None else f"${c['usd_per_turn']:.5f}"
    src = ", ".join(f"{k} {v}" for k, v in sorted(c["sources"].items()))
    lines.append("")
    lines.append(f"cost ${c['usd']:.4f} over {c['turns']} turns, {per} per turn (runs from: {src})")
    return "\n".join(lines)


def write_report(report: dict, out_dir: Path = LOGS_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"latency-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, indent=2))
    return path


def resolve_runs(refs: list[str], *, last: int = 0, ids_file: Path | None = None,
                 logs_dir: Path = LOGS_DIR) -> list[Path]:  # fmt: skip
    refs = list(refs)
    if ids_file:
        refs += [line.strip() for line in ids_file.read_text().splitlines() if line.strip()]
    paths = [find_log(r, logs_dir=logs_dir) for r in refs]
    if last:
        paths += all_logs(logs_dir)[-last:]
    seen, out = set(), []
    for p in paths:
        if p.resolve() not in seen:
            seen.add(p.resolve())
            out.append(p)
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="*", help="event log paths or run ids")
    ap.add_argument("--last", type=int, default=0, help="add the N newest runs in logs/")
    ap.add_argument("--from", dest="ids_file", type=Path, help="a file of run ids, one per line")
    ap.add_argument("--warmup", type=int, default=WARMUP_TURNS, help="turns dropped from each session")
    ap.add_argument("--ledger", type=Path, default=LEDGER_PATH)
    ap.add_argument("--logs-dir", type=Path, default=LOGS_DIR)
    ap.add_argument("--no-json", action="store_true", help="print the table only")
    args = ap.parse_args(argv)
    paths = resolve_runs(args.runs, last=args.last, ids_file=args.ids_file, logs_dir=args.logs_dir)
    if not paths:
        ap.error("name event logs or run ids, or pass --last N")
    report = build_report(paths, warmup=args.warmup, ledger=args.ledger)
    print(format_table(report))
    if not args.no_json:
        print(f"report: {write_report(report, args.logs_dir)}")


if __name__ == "__main__":
    main()
