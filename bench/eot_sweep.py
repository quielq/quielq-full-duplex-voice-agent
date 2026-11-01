"""End-of-turn tuning: the same spoken clips under each Flux setting.

Clips come from macOS `say` at run time, so no recordings are stored. They are synthetic
speech, and the timings are framework events measured from the last loud sample of the
clip: use them to compare settings, not as headline latency.

    uv run python -m bench.eot_sweep --grid eot_threshold=0.7 --repeat 1      # fake STT, no spend
    ME3_LIVE=1 uv run python -m bench.eot_sweep --stt deepgram_flux \
        --grid eot_threshold=0.5,0.6,0.7,0.8 --repeat 3                       # about $0.09
    ME3_LIVE=1 uv run python -m bench.eot_sweep --stt deepgram_flux --llm groq \
        --grid eager_eot_threshold=off,0.4,0.5 --repeat 2
    ME3_LIVE=1 uv run python -m bench.eot_sweep --stt deepgram_flux \
        --grid turn_stop=flux,flux_forced --repeat 3                          # about $0.04

Each clip kind answers one question. "command" and "chat" give the wait; "pause" has a
silence mid-sentence, so a turn that ends inside it cut the speaker off.
"""

from __future__ import annotations

import argparse
import array
import asyncio
import json
import os
import re
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

from harness.bot import run_bot
from harness.config import load_config
from harness.file_input import read_pcm

LOUD = 500  # same threshold as harness.file_input.speech_onset_s
CLIPS = [
    ("command", "Set a timer for five minutes."),
    ("command", "What time is it right now?"),
    ("command", "Add a note to buy milk."),
    ("command", "Turn off the kitchen lights."),
    ("chat", "Tell me a joke about cats."),
    ("chat", "What is the capital of France?"),
    ("chat", "Why is the sky blue?"),
    ("chat", "Explain how a rainbow forms in one short sentence, and please keep it simple."),
    ("pause", "Set a timer for [[slnc 900]] five minutes."),
    ("pause", "Add a note to call my [[slnc 900]] mother tomorrow."),
    ("pause", "What is the capital of [[slnc 1200]] Japan?"),
    ("pause", "Turn off the lights in the [[slnc 1200]] living room."),
]


def _say(text: str, path: Path) -> Path:
    subprocess.run(["say", "-o", str(path), "--data-format=LEI16@16000", text], check=True)
    return path


def trailing_pad_s(path: Path) -> float:
    """Seconds of near-silence after the last loud sample (`say` files end with a pad)."""
    pcm, rate = read_pcm(path)
    samples = array.array("h", pcm)
    for i in range(len(samples) - 1, -1, -1):
        if abs(samples[i]) >= LOUD:
            return (len(samples) - 1 - i) / rate
    return 0.0


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z']+", re.sub(r"\[\[.*?\]\]", "", text).lower())


def clip_row(events: list[dict], pad_s: float, text: str) -> dict:
    """One clip's result. A cut is a turn that ended before the speech did, ended more than
    once, or left the clip's last word unheard because the run stopped on a reply to a fragment.
    """
    speech_end = next(e["t_ns"] for e in events if e["event"] == "injection_speech_end") - int(pad_s * 1e9)
    ends = [e["t_ns"] for e in events if e["event"] == "end_of_turn"]
    audio = next((e["t_ns"] for e in events if e["event"] == "tts_first_byte" and e["t_ns"] >= speech_end), None)
    finals = [e for e in events if e["event"] == "asr_final"]
    forced_at = next((e["t_ns"] for e in events if e["event"] == "force_end_turn" and e["sent"]), None)
    final_after_force = next((e["t_ns"] for e in finals if forced_at and e["t_ns"] >= forced_at), None)
    heard = _words(" ".join(e["text"] for e in finals))
    count = lambda name: sum(e["event"] == name for e in events)  # noqa: E731
    return {
        "eot_ms": round((ends[-1] - speech_end) / 1e6) if ends else None,
        "reply_ms": round((audio - speech_end) / 1e6) if audio else None,
        "cut": len(ends) > 1 or any(t < speech_end for t in ends) or heard[-1:] != _words(text)[-1:],
        "transcript": " ".join(e["text"] for e in finals),
        "triggers": [e.get("trigger") for e in finals],
        "eager": count("eager_eot"),
        "withdrawn": count("eager_cancel"),
        # A held speculative call logs llm_request only if released, so add the withdrawn ones.
        "llm_calls": count("llm_request") + count("eager_cancel"),
        "timed_out": count("reply_timeout") > 0,
        "forced": sum(e["event"] == "force_end_turn" and e["sent"] for e in events),
        "force_ms": round((final_after_force - forced_at) / 1e6) if forced_at and final_after_force else None,
    }


def _value(name: str, raw: str) -> float | int | None:
    if name == "turn_stop":
        return raw
    if name == "reply_templates":
        return raw == "on"
    if raw == "off":
        return None
    return int(raw) if name == "eot_timeout_ms" else float(raw)


async def run_clip(args, name: str, value, kind: str, text: str, wav: Path, workdir: Path) -> dict:
    cfg = load_config("mac")  # real ledger path, so live spend counts against the caps
    cfg.providers["stages"] = {"stt": args.stt, "llm": args.llm, "tts": "fake"}
    cfg.providers["providers"]["deepgram"]["flux"].update(args.fixed)
    if name == "reply_templates":
        cfg.tool_policy["reply_templates"]["enabled"] = value
    elif name == "turn_stop":
        cfg.profile["turn"]["stop"] = value
    else:
        cfg.providers["providers"]["deepgram"]["flux"][name] = value
    result = await run_bot(
        cfg,
        input_mode="wav",
        wav=wav,
        stt_script=[text.replace("[[slnc 900]] ", "").replace("[[slnc 1200]] ", "")],
        data_dir=str(workdir / "data"),
        handle_sigint=False,
        reply_timeout_s=20,
        last_reply_timeout_s=8,  # a live run streams billed silence while it waits
    )
    events = [json.loads(line) for line in Path(result.events_path).read_text().splitlines()]
    return {
        name: value, "kind": kind, "text": text, "run_id": result.run_id,
        "cost_usd": round(result.session_usd, 5), **clip_row(events, trailing_pad_s(wav), text),
    }  # fmt: skip


def _p50(rows: list[dict], key: str) -> int | None:
    values = [r[key] for r in rows if r[key] is not None]
    return round(statistics.median(values)) if values else None


def summarise(rows: list[dict], name: str) -> list[dict]:
    """One line per setting and clip kind."""
    out = []
    for value in dict.fromkeys(r[name] for r in rows):
        for kind in ("command", "chat", "pause"):
            group = [r for r in rows if r[name] == value and r["kind"] == kind]
            if group:
                out.append({
                    name: value, "kind": kind, "n": len(group),
                    "eot_p50": _p50(group, "eot_ms"), "reply_p50": _p50(group, "reply_ms"),
                    "cuts": sum(r["cut"] for r in group),
                    "no_reply": sum(r["reply_ms"] is None for r in group),
                    "timeout_ends": sum("timeout" in r["triggers"] for r in group),
                    "eager": sum(r["eager"] for r in group),
                    "withdrawn": sum(r["withdrawn"] for r in group),
                    "llm_calls": sum(r["llm_calls"] for r in group),
                    "force_p50": _p50(group, "force_ms"),
                    "usd": round(sum(r["cost_usd"] for r in group), 4),
                })  # fmt: skip
    return out


async def sweep(args) -> list[dict]:
    name, _, raw = args.grid.partition("=")
    values = [_value(name, v) for v in raw.split(",")]
    rows = []
    out = Path(load_config("mac").path("logs_dir")) / f"eot_sweep-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    with tempfile.TemporaryDirectory() as d, open(out, "a", buffering=1) as f:
        workdir = Path(d)
        clips = [c for c in CLIPS if not args.kinds or c[0] in args.kinds.split(",")]
        wavs = [_say(text, workdir / f"clip{i}.wav") for i, (_, text) in enumerate(clips)]
        for repeat in range(args.repeat):
            for value in values:
                for (kind, text), wav in zip(clips, wavs, strict=True):
                    row = await run_clip(args, name, value, kind, text, wav, workdir / str(len(rows)))
                    row["repeat"] = repeat
                    rows.append(row)
                    f.write(json.dumps(row) + "\n")
    print(f"rows: {out}")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--grid", required=True, help="one Flux setting and its values, 'off' for unset")
    ap.add_argument("--set", default="", help="Flux settings held fixed, e.g. eot_threshold=0.6")
    ap.add_argument("--stt", choices=["fake", "deepgram_flux"], default="fake")
    ap.add_argument("--llm", choices=["fake", "groq"], default="fake")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--kinds", default="", help="only these clip kinds, e.g. command")
    args = ap.parse_args()
    if (args.stt != "fake" or args.llm != "fake") and os.environ.get("ME3_LIVE") != "1":
        raise SystemExit("this run spends money: set ME3_LIVE=1")
    args.fixed = {}
    for item in filter(None, args.set.split(",")):
        key, _, raw = item.partition("=")
        args.fixed[key] = _value(key, raw)
    rows = asyncio.run(sweep(args))
    name = args.grid.partition("=")[0]
    for line in summarise(rows, name):
        print(json.dumps(line))
    print(f"total ${sum(r['cost_usd'] for r in rows):.4f}")


if __name__ == "__main__":
    main()
