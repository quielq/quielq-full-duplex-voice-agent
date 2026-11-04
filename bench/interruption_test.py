"""Step 4: after a barge-in, does the LLM context hold only the words actually spoken?

Scenario: the user asks for a story; about ``--barge-after`` seconds after the bot starts
speaking, the user says "stop right there". Speech is synthesised with macOS `say` and
streamed in real time through Silero VAD and Smart Turn; STT and LLM are fakes so only
the TTS varies. Output goes to a silent output paced like a sound card.

    uv run python -m bench.interruption_test --tts fake_words
    uv run python -m bench.interruption_test --tts deepgram_aura2     # spends about $0.02

Reported per run:
* heard_words: words whose audio fully played before the interruption. With word
  timestamps, a word counts once the next word has started playing (``heard_source``
  is ``word_timestamps``). Without them it falls back to played seconds and a pace:
  exact for the fakes, 850 characters per minute for real TTS (``pace``). The pace
  guess undercounts a fast voice: Cartesia speaks about 1,000 to 1,150 a minute, which
  made the Nov 3 Cartesia runs look 3 to 4 words worse than they were (research.md);
* committed_words: words in the assistant message that went into the context;
* overcommit_words: committed minus heard (the word in progress plus one released early);
* stop_ms: barge-in speech onset to the last voiced output sample.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import tempfile
from pathlib import Path

from harness.bot import run_bot
from harness.config import load_config
from harness.fakes import STORY
from harness.file_input import Segment

REQUEST = "Tell me a story about a lighthouse keeper."
BACKCHANNEL_WAIT_S = 15
BARGE_IN = "Stop right there."  # no internal pause, so VAD sees one utterance


def _say(text: str, path: Path) -> Path:
    subprocess.run(["say", "-o", str(path), "--data-format=LEI16@16000", text], check=True)
    return path


def _assistant_text_after_story(messages: list[dict]) -> str:
    """Content of the assistant message that directly follows the story request ("" if none)."""
    seen_request = False
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        if seen_request:
            return content or "" if m.get("role") == "assistant" else ""
        if m.get("role") == "user" and "story" in (content or "").lower():
            seen_request = True
    return ""


def _events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def fully_heard_words(text: str, heard_chars: float) -> int:
    """Words that ended at or before this many characters of playback."""
    return sum(word.end() <= heard_chars for word in re.finditer(r"\S+", text))


def pace_sec_per_char(tts: str) -> float:
    """Seconds of audio per character, for TTS without word timestamps.

    The plain fakes pace exactly 60 ms per character. For anything else, generation may be
    cut off by the interruption, so use a typical speaking pace of 850 characters per audio
    minute (review: 750 to 900). Cartesia is faster than this, so prefer word timestamps.
    """
    return 0.060 if tts in ("fake", "fake_words") else 60 / 850


def timestamp_heard_words(word_starts_s: list[float], heard_s: float) -> int:
    """Words whose audio finished within ``heard_s`` seconds of the first word starting.

    ``word_starts_s`` are the TTS's own start offsets, in order. A word has finished when
    the next one starts, so the last word reported is never counted as heard.
    """
    return sum(nxt <= heard_s + 1e-6 for nxt in word_starts_s[1:])


def _story_word_starts(out, story_ctx) -> list[float]:
    """Start offsets of the story's timed words, relative to its first word, in seconds.

    Punctuation-only tokens belong to the word before them and are skipped.
    """
    pts = [w.pts_ns for w in getattr(out, "words", [])
           if w.context_id == story_ctx and re.search(r"\w", w.text)]  # fmt: skip
    return [(p - pts[0]) / 1e9 for p in pts] if pts else []


def analyse(result, full_text: str, tts: str) -> dict:
    out = result.output
    story_ctx = next(iter(out.generated_s), None)  # first TTS context is the story
    generated = out.generated_s.get(story_ctx, 0.0)
    committed = _assistant_text_after_story(result.context_messages)
    ev = _events(result.events_path)
    barge = next((e for e in ev if e["event"] == "injection_speech_start" and e.get("segment") == 1), None)
    story_start = next((e for e in ev if e["event"] == "first_audio_out_acoustic"), None)
    cut_ns = next((t for t in out.interruptions_ns if barge and t >= barge["t_ns"]), None)
    played = (
        out.voiced_s_between(story_start["play_t_ns"], cut_ns) if story_start and cut_ns else 0.0
    )

    after_barge = [e for e in ev if barge and e["t_ns"] >= barge["t_ns"]]
    # Measure from the first loud sample of the barge-in, not the start of the file.
    onset_ns = barge["t_ns"] + int(barge.get("onset_offset_s", 0) * 1e9) if barge else None
    stopped = next((e for e in after_barge if e["event"] == "audio_stopped_last_voiced"), None)

    # Pace estimate: played seconds to characters.
    sec_per_char = pace_sec_per_char(tts)
    heard_words_pace = fully_heard_words(full_text, played / sec_per_char)
    # Word timestamps: the first word starts at the first loud sample, and playback ends at
    # the last loud sample before the cut. Wall time, so pauses between sentences count.
    starts = _story_word_starts(out, story_ctx)
    end_ns = stopped["voiced_t_ns"] if stopped else cut_ns
    if starts and story_start and end_ns:
        heard_s = (end_ns - story_start["play_t_ns"]) / 1e9
        heard_words, heard_source = timestamp_heard_words(starts, heard_s), "word_timestamps"
    else:
        heard_words, heard_source = heard_words_pace, "pace"
    at_output = next((e for e in after_barge if e["event"] == "interruption_at_output"), None)
    vad = [e for e in after_barge if e["event"] == "vad_onset"]

    return {
        "run_id": result.run_id,
        "full_words": len(full_text.split()),
        "generated_s": round(generated, 2),
        "played_s": round(played, 2),
        "heard_words": max(0, heard_words),
        "heard_source": heard_source,
        "heard_words_pace": heard_words_pace,
        "committed_words": len(committed.split()),
        "overcommit_words": len(committed.split()) - max(0, heard_words),
        "committed_text": committed,
        "stop_ms": round((stopped["voiced_t_ns"] - onset_ns) / 1e6) if barge and stopped else None,
        "vad_onset_after_speech_ms": round((vad[0]["t_ns"] - onset_ns) / 1e6) if vad else None,
        "vad_to_output_flush_ms": round((at_output["t_ns"] - vad[0]["t_ns"]) / 1e6)
        if vad and at_output and at_output["t_ns"] >= vad[0]["t_ns"]
        else None,
        "cost_usd": round(result.session_usd, 5),
    }


def _no_reply_expected(cfg, text: str, barge_in: str) -> bool:
    b = cfg.profile["barge_in"]
    words = re.findall(r"[a-z'-]+", text.lower())
    return barge_in == "duck" and len(words) <= b["backchannel_max_words"] and not set(words) & set(b["allowlist"])


async def run(
    tts: str,
    barge_after_s: float,
    workdir: Path,
    *,
    barge_text: str = BARGE_IN,
    barge_in: str = "immediate",
    stt: str = "fake",
) -> dict:
    cfg = load_config("mac")
    cfg.providers["stages"] = {"stt": stt, "llm": "fake", "tts": tts}
    cfg.profile["barge_in"]["mode"] = barge_in
    request = _say(REQUEST, workdir / "request.wav")
    barge = _say(barge_text, workdir / "barge.wav")
    segments = [Segment(request), Segment(barge, after="bot_speaking", delay_s=barge_after_s)]
    result = await run_bot(
        cfg,
        input_mode="wav",
        segments=segments,
        stt_script=[REQUEST.lower(), barge_text.lower()],
        data_dir=str(workdir / "data"),
        handle_sigint=False,
        reply_timeout_s=60,
        # A backchannel gets no new reply, and a live run streams billed silence while it waits.
        last_reply_timeout_s=BACKCHANNEL_WAIT_S if _no_reply_expected(cfg, barge_text, barge_in) else None,
    )
    report = analyse(result, STORY, tts)
    report.update(_duck_report(result, report))
    return report


def _duck_report(result, report: dict) -> dict:
    """Duck timings: speech onset and VAD onset to the first quieter write, and the decision."""
    ev = _events(result.events_path)
    barge = next((e for e in ev if e["event"] == "injection_speech_start" and e.get("segment") == 1), None)
    if not barge:
        return {}
    onset_ns = barge["t_ns"] + int(barge.get("onset_offset_s", 0) * 1e9)
    after = [e for e in ev if e["t_ns"] >= barge["t_ns"]]
    vad = next((e for e in after if e["event"] == "vad_onset"), None)
    applied = next((e for e in after if e["event"] == "gain_applied" and e["gain"] < 1), None)
    decided = next((e for e in after if e["event"] in ("barge_in_interrupt", "unduck")), None)
    resumed = any(e["event"] == "unduck" for e in after)
    return {
        "duck_after_speech_ms": round((applied["write_t_ns"] - onset_ns) / 1e6) if applied else None,
        "duck_after_vad_ms": round((applied["write_t_ns"] - vad["t_ns"]) / 1e6) if applied and vad else None,
        "decision": (decided["event"] + ":" + decided.get("reason", "")) if decided else None,
        "decide_ms": decided.get("decide_ms") if decided else None,
        "bot_kept_talking": resumed and report.get("stop_ms") is None,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tts", default="fake_words",
                    choices=["fake", "fake_words", "fake_words_fast", "deepgram_aura2", "cartesia", "voice_speak"])  # fmt: skip
    ap.add_argument("--barge-after", type=float, default=2.5)
    ap.add_argument("--barge-text", default=BARGE_IN)
    ap.add_argument("--barge-in", choices=["immediate", "duck"], default="immediate")
    ap.add_argument("--stt", choices=["fake", "deepgram_flux", "voice_listen"], default="fake",
                    help="deepgram_flux gives interim transcripts (spends about $0.001 a run)")  # fmt: skip
    args = ap.parse_args()
    with tempfile.TemporaryDirectory() as d:
        report = asyncio.run(run(args.tts, args.barge_after, Path(d),
                                 barge_text=args.barge_text, barge_in=args.barge_in,
                                 stt=args.stt))  # fmt: skip
    report["tts"] = args.tts
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
