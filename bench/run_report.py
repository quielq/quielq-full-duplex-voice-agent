"""Explain one run from its event log: a timeline per turn, plus anything that looks wrong.

    uv run python -m bench.run_report                  # the latest run
    uv run python -m bench.run_report --live           # the latest run with a real provider
    uv run python -m bench.run_report 20261031-163553  # by run id, or part of one
    uv run python -m bench.run_report logs/events-x.jsonl --all
    uv run python -m bench.run_report --list 20        # one line per recent run
    uv run python -m bench.run_report --json

Reads logs only: no provider calls, no spend. Times are framework events, so use them to
attribute a problem, not as headline latency (bench/latency_probe.py and ADR-009 own that).
The anomaly rules below are for debugging; their thresholds are not targets.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from harness.config import REPO_ROOT

LOGS_DIR = REPO_ROOT / "logs"

# Hidden from the timeline unless --all: frequent, and summarized in the anomalies instead.
NOISY = {"audio_hop", "echo_window", "gain_applied", "tool_policy", "first_audio_out_acoustic", "system_stats"}

SLOW_REPLY_MS = 2000  # end of turn to audio
OUTPUT_GAP_MS = 300  # a speaker write this long is a hole in speech that people hear
STARVED_RATIO = 0.9  # audio arriving slower than this while the backlog is near empty
STARVED_BACKLOG_MS = 200
MIN_STARVED_WINDOWS = 2
MAX_STARVED_WINDOW_MS = 1200  # logs before Nov 3 stretched a window across the wait between replies
OPEN_TURN_MS = 10_000  # a turn this close to run_end was cut off, not a phantom
TOOL_FAILURES = {"timeout", "tool_error", "commit_failed", "denied_unknown"}


@dataclass
class Anomaly:
    severity: str  # error | warn
    code: str
    turn: int | None
    detail: str


@dataclass
class Turn:
    turn: int
    heard: str = ""
    said: str = ""
    tools: list[str] = field(default_factory=list)
    reply_ms: float | None = None
    interrupted: bool = False
    spoke: bool = False
    cut_off: bool = False  # the user went on talking before a reply was due


@dataclass
class Report:
    run_id: str
    path: str
    stages: dict
    input: str
    usd: float | None
    finished: bool
    seconds: float
    turns: list[Turn]
    anomalies: list[Anomaly]

    @property
    def live(self) -> bool:
        return is_live(self.stages)


def is_live(stages: dict) -> bool:
    return any(not str(v).startswith("fake") for v in stages.values())


def load_events(path: Path) -> list[dict]:
    """Read a log, skipping a line cut short by a crash."""
    events = []
    with open(path) as f:
        for line in f:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def _turns(events: list[dict]) -> list[Turn]:
    turns: dict[int, Turn] = {}
    end_of_turn: dict[int, int] = {}
    speaking_in: int | None = None
    for e in events:
        tid, name = e.get("turn_id", 0), e["event"]
        # A new user turn logs an interruption whether or not the bot was talking, and the
        # event lands on the new turn, so credit it to the turn whose speech it cut.
        if name == "first_audio_out":
            speaking_in = tid
        elif name == "audio_silent":
            speaking_in = None
        elif name in ("interruption", "barge_in_interrupt") and speaking_in is not None:
            turns[speaking_in].interrupted = True
            speaking_in = None
        if tid == 0:
            continue
        t = turns.setdefault(tid, Turn(tid))
        if name == "user_turn_start" and tid - 1 in end_of_turn:
            if (e["t_ns"] - end_of_turn[tid - 1]) / 1e6 < SLOW_REPLY_MS:
                turns[tid - 1].cut_off = True
        if name in ("asr_final", "user_text"):
            t.heard = f"{t.heard} {e.get('text', '')}".strip()
        elif name == "reply_text":
            t.said = f"{t.said} {e.get('text', '')}".strip()
        elif name == "tool_call_start":
            t.tools.append(e.get("name", "?"))
        elif name == "end_of_turn":
            end_of_turn.setdefault(tid, e["t_ns"])
        elif name == "tool_policy":
            t.spoke = True  # a held action's answer is spoken by the harness, unlogged
        elif name in ("first_audio_out", "tts_request", "tts_first_byte"):
            t.spoke = True
            if name == "first_audio_out" and tid in end_of_turn and t.reply_ms is None:
                t.reply_ms = (e["t_ns"] - end_of_turn[tid]) / 1e6
    return [turns[k] for k in sorted(turns)]


def _starved_windows(events: list[dict]) -> list[dict]:
    """Windows where speech was playing and arrival, not the speaker, ran short (ADR-015)."""
    out = []
    for e in events:
        if e["event"] != "audio_hop" or not e.get("written_ms"):
            continue
        ratio = e["arrived_ms"] / max(e["window_ms"], 1)
        draining = e["arrived_ms"] == 0  # the end of a reply: everything has already arrived
        if ratio < STARVED_RATIO and e["backlog_ms"] < STARVED_BACKLOG_MS and not draining \
                and e["window_ms"] <= MAX_STARVED_WINDOW_MS:
            out.append(e)
    return out


def find_anomalies(events: list[dict], turns: list[Turn]) -> list[Anomaly]:
    found: list[Anomaly] = []

    def add(severity: str, code: str, turn: int | None, detail: str) -> None:
        found.append(Anomaly(severity, code, turn, detail))

    names = Counter(e["event"] for e in events)
    if "run_end" not in names:
        add("error", "no_run_end", None, "the log stops without run_end: crash, kill, or still running")
    for e in events:
        tid, name = e.get("turn_id"), e["event"]
        if name == "pipeline_error":
            add("error", "pipeline_error", tid, str(e.get("error", ""))[:200])
        elif name == "reply_timeout":
            add("error", "reply_timeout", tid, "no reply before the timeout")
        elif name == "spend_cap_reached":
            add("error", "spend_cap", tid, f"{e.get('provider')} reached its daily cap")
        elif name == "tts_cleared" and e.get("timed_out"):
            add("warn", "tts_clear_timeout", tid, f"stale audio clear took {e.get('clear_ms')} ms (ADR-012)")
        elif name == "tool_policy" and e.get("decision") in TOOL_FAILURES:
            detail = e.get("error", "") or e.get("result", "") or e.get("decision")
            add("error", "tool_failed", tid, f"{e.get('tool')}: {detail}"[:200])

    # Gaps between sentences and during tools are pauses, so only a stalled write counts.
    gaps = [e["write_max_ms"] for e in events
            if e["event"] == "audio_hop" and e.get("write_max_ms", 0) >= OUTPUT_GAP_MS]  # fmt: skip
    if gaps:
        add("warn", "output_gap", None, f"{len(gaps)} s with a stalled speaker write, worst {max(gaps)} ms")

    for t in turns:
        if t.heard and not t.spoke and not t.tools and not t.cut_off:
            add("error", "silent_turn", t.turn, f"heard {t.heard[:60]!r}, said nothing")
        if t.reply_ms is not None and t.reply_ms > SLOW_REPLY_MS:
            add("warn", "slow_reply", t.turn, f"{t.reply_ms:.0f} ms from end of turn to audio")

    turn_ids = {e["turn_id"] for e in events if e["event"] == "user_turn_start"}
    heard = {t.turn for t in turns if t.heard}
    run_end = next((e["t_ns"] for e in events if e["event"] == "run_end"), None)
    if turn_ids and run_end is not None:
        last = max(turn_ids)
        started = max(e["t_ns"] for e in events if e["event"] == "user_turn_start" and e["turn_id"] == last)
        if (run_end - started) / 1e6 < OPEN_TURN_MS:
            turn_ids.discard(last)
    if phantom := sorted(turn_ids - heard):
        add("warn", "phantom_turn", phantom[0],
            f"{len(phantom)} turn(s) started with no transcript: echo or noise? turns {phantom[:8]}")  # fmt: skip

    starved = _starved_windows(events)
    if len(starved) >= MIN_STARVED_WINDOWS:
        arrived = sum(e["arrived_ms"] for e in starved)
        window = sum(e["window_ms"] for e in starved)
        add("warn", "tts_starved", starved[0].get("turn_id"),
            f"{len(starved)} s of speech arrived at {arrived / window:.2f}x real time (ADR-015)")  # fmt: skip

    open_tools: list[dict] = []  # a started tool must end with a result or a cancel
    for e in events:
        if e["event"] == "tool_call_start":
            open_tools.append(e)
        elif e["event"] in ("tool_call_end", "tool_call_cancelled"):
            match = next((o for o in open_tools if o.get("name") == e.get("name")), None)
            if match is not None:
                open_tools.remove(match)
            if e["event"] == "tool_call_cancelled":
                add("warn", "tool_cancelled", e.get("turn_id"), f"{e.get('name')} cancelled before it returned")
    for o in open_tools:
        add("error", "tool_no_end", o.get("turn_id"), f"{o.get('name')} started and never returned or cancelled")

    played = {e["turn_id"] for e in events if e["event"] == "filler_trigger"}
    ended = {e["turn_id"] for e in events if e["event"] == "end_of_turn"}
    if len(ended) >= 3 and len(played & ended) > len(ended) / 2:
        add("warn", "filler_overuse", None, f"waiting phrase on {len(played & ended)} of {len(ended)} turns")
    return found


def build_report(path: Path) -> Report:
    events = load_events(path)
    start = next((e for e in events if e["event"] == "run_start"), {})
    end = next((e for e in events if e["event"] == "run_end"), None)
    turns = _turns(events)
    seconds = (events[-1]["t_ns"] - events[0]["t_ns"]) / 1e9 if events else 0.0
    return Report(
        run_id=events[0]["run_id"] if events else path.stem,
        path=str(path),
        stages=start.get("stages", {}),
        input=start.get("input", "?"),
        usd=end.get("session_usd") if end else None,
        finished=end is not None,
        seconds=seconds,
        turns=turns,
        anomalies=find_anomalies(events, turns) if events else [Anomaly("error", "empty_log", None, "no events")],
    )


def all_logs(logs_dir: Path = LOGS_DIR) -> list[Path]:
    """Oldest first. Run ids start with the time, so the name sorts by date."""
    return sorted(logs_dir.glob("events-*.jsonl"))


def find_log(ref: str | None, *, live: bool = False, logs_dir: Path = LOGS_DIR) -> Path:
    if ref and Path(ref).is_file():
        return Path(ref)
    logs = all_logs(logs_dir)
    if ref:
        logs = [p for p in logs if ref in p.name]
    if live:
        logs = [p for p in logs if _run_is_live(p)]
    if not logs:
        raise SystemExit(f"no matching run in {logs_dir}")
    return logs[-1]


def _run_is_live(path: Path) -> bool:
    with open(path) as f:
        for line in f:
            if '"run_start"' in line:
                return is_live(json.loads(line).get("stages", {}))
    return False


def timeline(events: list[dict], *, show_all: bool = False) -> list[str]:
    """One line per event, grouped by turn, in seconds from the first event."""
    if not events:
        return []
    t0 = events[0]["t_ns"]
    lines, turn = [], None
    skip = {"run_id", "turn_id", "event", "t_ns"}
    for e in events:
        if not show_all and e["event"] in NOISY:
            continue
        if e.get("turn_id") != turn:
            turn = e.get("turn_id")
            lines.append(f"-- turn {turn}")
        fields = " ".join(f"{k}={v!r}" for k, v in e.items() if k not in skip)
        lines.append(f"{(e['t_ns'] - t0) / 1e9:8.3f}  {e['event']:<24} {fields[:110]}".rstrip())
    return lines


def print_report(report: Report, events: list[dict], *, show_all: bool) -> None:
    usd = "unknown" if report.usd is None else f"${report.usd:.4f}"
    print(f"run {report.run_id}  input={report.input}  {report.seconds:.1f} s  cost {usd}")
    print(f"  stages: {report.stages}")
    print(f"  log:    {report.path}")
    print("\nTurns")
    for t in report.turns:
        reply = f"{t.reply_ms:6.0f} ms" if t.reply_ms is not None else "     none"
        tools = f" tools={t.tools}" if t.tools else ""
        cut = " interrupted" if t.interrupted else ""
        print(f"  {t.turn:>3}  reply {reply}  {t.heard[:60]!r}{tools}{cut}")
        if t.said:
            print(f"                     said {t.said[:80]!r}")
    print("\nTimeline")
    for line in timeline(events, show_all=show_all):
        print(f"  {line}")
    print("\nAnomalies")
    if not report.anomalies:
        print("  none found")
    for a in report.anomalies:
        where = f"turn {a.turn}" if a.turn is not None else "run"
        print(f"  {a.severity.upper():<5} {a.code:<18} {where:<8} {a.detail}")


def print_list(paths: list[Path]) -> None:
    for path in paths:
        r = build_report(path)
        kinds = Counter(a.code for a in r.anomalies)
        worst = " ".join(f"{k}x{v}" for k, v in kinds.most_common(4)) or "clean"
        stages = ",".join(str(v) for v in r.stages.values())
        print(f"{r.run_id}  {r.input:<4} {len(r.turns):>3} turns  {stages:<40} {worst}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", nargs="?", help="log path, run id, or part of one; default the latest")
    ap.add_argument("--live", action="store_true", help="only runs with a real provider")
    ap.add_argument("--all", action="store_true", help="include high-rate events in the timeline")
    ap.add_argument("--list", type=int, metavar="N", help="one line for each of the last N runs")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.list:
        logs = [p for p in all_logs() if not args.live or _run_is_live(p)]
        print_list(logs[-args.list :])
        return
    path = find_log(args.run, live=args.live)
    report = build_report(path)
    if args.json:
        print(json.dumps(asdict(report), indent=2, default=str))
    else:
        print_report(report, load_events(path), show_all=args.all)


if __name__ == "__main__":
    main()
