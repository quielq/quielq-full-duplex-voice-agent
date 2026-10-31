"""Latency probe (skeleton).

Today: reads one run's event log and prints per-turn stage deltas from framework
events. These are for attribution only.

Still to build (do not treat these numbers as headline latency until then):
* acoustic capture of the output device, so "first audio out" is the first
  non-silent sample, not BotStartedSpeakingFrame;
* stop latency from a loopback mic;
* p50/p90/p95 over 30+ repeats, with the first three turns of a session discarded.

    uv run python -m bench.latency_probe logs/events-<run_id>.jsonl
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

# (name, from_event, to_event). "last" picks the latest occurrence in the turn, "first" the earliest.
STAGES = [
    ("end_of_turn_after_speech", ("first", "injection_speech_end"), ("first", "end_of_turn")),
    ("vad_release", ("first", "injection_speech_end"), ("first", "vad_end")),
    ("tool_call", ("first", "tool_call_start"), ("first", "tool_call_end")),
    ("llm_first_token", ("last", "llm_request"), ("first", "llm_first_token")),
    ("tts_first_byte", ("first", "llm_first_token"), ("first", "tts_first_byte")),
    ("speech_end_to_tts_first_byte", ("first", "injection_speech_end"), ("first", "tts_first_byte")),
    ("speech_end_to_audio_out", ("first", "injection_speech_end"), ("first", "first_audio_out")),
    # Reported apart: the waiting phrase never counts toward the latency target (ADR-009).
    ("end_of_turn_to_filler", ("first", "end_of_turn"), ("first", "filler_audio_out")),
]


def load(path: Path) -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f]


def stage_deltas(events: list[dict]) -> dict[int, dict[str, float]]:
    """Per turn, milliseconds between event pairs. Injection events belong to the turn they precede."""
    by_turn: dict[int, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    pending_injection: dict[str, list[int]] = defaultdict(list)
    for e in events:
        if e["event"].startswith("injection_"):
            pending_injection[e["event"]].append(e["t_ns"])
            continue
        by_turn[e["turn_id"]][e["event"]].append(e["t_ns"])
    # A file injection belongs to the first real turn.
    if pending_injection:
        first_turn = min((t for t in by_turn if t > 0), default=0)
        for k, v in pending_injection.items():
            by_turn[first_turn][k].extend(v)

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


def main() -> None:
    path = Path(sys.argv[1])
    events = load(path)
    for turn, row in stage_deltas(events).items():
        print(f"turn {turn}")
        for k, v in row.items():
            print(f"  {k:<30} {v:8.1f} ms")
    played, turns = filler_rate(events)
    if played:
        print(f"waiting phrase on {played} of {turns} turns")


if __name__ == "__main__":
    main()
