"""Ask the ME4 practice questions and score the answers, the references, and the time taken.

``bench/me4_corpus.py`` makes the documents and the gold questions, and
``bench/me4_cases/public_questions.yaml`` adds hand-checked ones on real reports (questions whose
files are not in the corpus are skipped). This asks each question in its
own scripted session (text in, no audio), reads the event log, and scores:

- **answer:** a number in the reply within the question's tolerance, or the gold's key words for
  text answers, or a plain "not in the documents" for questions the documents cannot answer;
- **cited:** each gold file is named in the reply (by its title words or file name), and its
  page or slide too when the gold has one;
- **retrieved:** each gold file appeared in a tool call or result, so a miss can be told apart
  from a search that never found the file;
- **time:** first audio and full reply after the question, from the event log's marks.

The rules are deliberately strict and simple, so a score never needs a judge to be read. An LLM
judge for answers the rules miss is a later step, and will go through the cost ledger.

    uv run python -m bench.me4_eval                          # fake LLM: checks the plumbing, scores ~0
    ME3_LIVE=1 uv run python -m bench.me4_eval --stages llm=anthropic --limit 10   # spends money
    uv run python -m bench.me4_eval --score data/me4_eval/runs/<run>.jsonl          # score again
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import yaml

from harness.agents import Agent
from harness.bot import run_bot
from harness.config import REPO_ROOT, load_config
from harness.mcp_tools import TASK_SERVERS
from servers.common import FAKE_TOOL_STAGES

EVAL_DIR = REPO_ROOT / "data" / "me4_eval"
GENERATED = EVAL_DIR / "me4_questions.json"  # from bench/me4_corpus.py
PUBLIC = REPO_ROOT / "bench" / "me4_cases" / "public_questions.yaml"  # hand-checked, real documents
RUNS = EVAL_DIR / "runs"

NOT_FOUND = re.compile(
    r"\b(not|no|isn't|aren't|doesn't|don't|couldn't|cannot|can't)\b[^.]{0,60}"
    r"\b(document|documents|files|mention|cover|covered|find|found|information|say|listed)\b",
    re.IGNORECASE,
)
STOP = {"the", "and", "for", "with", "that", "this", "from", "was", "were", "are", "its", "about",
        "times", "rate", "hourly", "days", "years", "months", "hours", "trucks", "php"}  # fmt: skip
UNITS = {"thousand": 1e3, "million": 1e6, "billion": 1e9}
SMALL = {w: i for i, w in enumerate(
    ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"])}  # fmt: skip
TENS = {w: 10 * i for i, w in enumerate(["_", "_", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]) if i > 1}


# Reading numbers out of a reply


def words_to_numbers(text: str) -> list[float]:
    """Numbers written as words, which a reply meant to be spoken often uses: "fourteen",
    "two hundred thirty-five thousand", "one point two five"."""
    out: list[float] = []
    words = re.findall(r"[a-z]+", text.lower().replace("-", " "))
    total = current = 0.0
    seen = False
    frac: str | None = None
    for w in [*words, "."]:
        if frac is not None and w in SMALL and SMALL[w] < 10:
            frac += str(SMALL[w])
            continue
        if frac is not None:
            current = float(f"{int(current)}.{frac}") if frac else current
            frac = None
        if w in SMALL:
            current += SMALL[w]
            seen = True
        elif w in TENS:
            current += TENS[w]
            seen = True
        elif w == "hundred" and seen:
            current *= 100
        elif w in UNITS and seen:
            total += current * UNITS[w]
            current = 0
        elif w == "point" and seen:
            frac = ""
        elif w == "and" and seen:
            continue
        else:
            if seen:
                out.append(total + current)
            total = current = 0.0
            seen = False
    return out


def numbers_in(text: str) -> list[float]:
    found = []
    for m in re.finditer(r"(\d[\d,]*(?:\.\d+)?)\s*(thousand|million|billion|[kmb]\b)?", text, re.IGNORECASE):
        raw, unit = m.group(1).replace(",", ""), (m.group(2) or "").lower()
        try:
            value = float(raw)
        except ValueError:
            continue
        scale = {"k": 1e3, "m": 1e6, "b": 1e9}.get(unit, UNITS.get(unit, 1))
        found.append(value * scale)
    return found + words_to_numbers(text)


# Scoring


def _near(gold: float, tolerance: float, found: list[float]) -> bool:
    """A whole-number gold (a count) is matched exactly; one written with a decimal point, such as
    5.0 percent, must not be matched by 5.3, so it gets only rounding room."""
    room = max(abs(gold) * tolerance, 0.5 if isinstance(gold, int) else 0.005)
    return any(abs(x - gold) <= room for x in found)


def answer_ok(q: dict, reply: str) -> bool:
    if q["kind"] == "unanswerable":
        return bool(NOT_FOUND.search(reply))
    if q.get("numbers"):  # every one of several numbers, e.g. two forecasts compared
        found = numbers_in(reply)
        return all(_near(g, q.get("tolerance", 0.0), found) for g in q["numbers"])
    if q.get("number") is not None:
        return _near(q["number"], q.get("tolerance", 0.0), numbers_in(reply))
    keys = [w for w in re.findall(r"[a-z0-9]+", q["answer"].lower()) if len(w) > 2 and w not in STOP]
    if not keys:
        return False
    low = reply.lower()
    return sum(k in low for k in keys) / len(keys) >= 0.6


def _file_words(path: str) -> list[str]:
    stem = Path(path).stem.lower()
    return [w for w in re.split(r"[_\-\s]+", stem) if len(w) > 2 and not w.isdigit()]


def file_named(path: str, text: str) -> bool:
    """The file by name, or all of its title words ("incident review", "fleet register"), so one
    word that also appears in the question ("employees") does not count as a citation."""
    low = text.lower()
    if Path(path).name.lower() in low or Path(path).stem.lower() in low:
        return True
    words = _file_words(path)
    return bool(words) and all(re.search(rf"\b{w}", low) for w in words)


def place_named(ref: dict, text: str) -> bool:
    low = text.lower()
    if ref.get("page"):
        return bool(re.search(rf"\b(page|p\.)\s*{ref['page']}\b", low))
    if ref.get("slide"):
        return bool(re.search(rf"\bslide\s*{ref['slide']}\b", low))
    if ref.get("sheet"):
        return ref["sheet"].lower() in low
    return True


def score(q: dict, rec: dict) -> dict:
    reply = rec.get("reply", "")
    tools = json.dumps(rec.get("tool_calls", []))
    refs = q["refs"]
    return {
        "id": q["id"],
        "kind": q["kind"],
        "answer": answer_ok(q, reply),
        "cited": all(file_named(r["file"], reply) and place_named(r, reply) for r in refs),
        "cited_file": all(file_named(r["file"], reply) for r in refs),
        "retrieved": all(Path(r["file"]).name in tools for r in refs),
        "first_audio_ms": rec.get("first_audio_ms"),
        "reply_ms": rec.get("reply_ms"),
        "usd": rec.get("usd", 0.0),
        "error": rec.get("error", ""),
    }


def pct(xs: list[float], p: float) -> float | None:
    xs = sorted(x for x in xs if x is not None)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None


def summary(rows: list[dict]) -> dict:
    def block(rs: list[dict]) -> dict:
        n = len(rs)
        return {
            "n": n,
            "answer": round(sum(r["answer"] for r in rs) / n, 3),
            "cited": round(sum(r["cited"] for r in rs) / n, 3),
            "cited_file": round(sum(r["cited_file"] for r in rs) / n, 3),
            "retrieved": round(sum(r["retrieved"] for r in rs) / n, 3),
            "first_audio_p50_ms": pct([r["first_audio_ms"] for r in rs], 0.5),
            "reply_p50_ms": pct([r["reply_ms"] for r in rs], 0.5),
            "reply_p90_ms": pct([r["reply_ms"] for r in rs], 0.9),
        }

    kinds = defaultdict(list)
    for r in rows:
        kinds[r["kind"]].append(r)
    return {
        "all": block(rows),
        "usd": round(sum(r["usd"] for r in rows), 4),
        "errors": sum(bool(r["error"]) for r in rows),
        "by_kind": {k: block(v) for k, v in sorted(kinds.items())},
    }


# Running


def timings(events_path: Path) -> tuple[float | None, float | None]:
    """Milliseconds from the question to the first TTS audio, and to the last reply text."""
    asked = first_audio = last_reply = None
    for line in events_path.read_text().splitlines():
        e = json.loads(line)
        kind = e.get("event")
        if kind == "user_text" and asked is None:
            asked = e["t_ns"]
        elif kind == "tts_first_byte" and asked is not None and first_audio is None:
            first_audio = e["t_ns"]
        elif kind == "reply_text" and asked is not None:
            last_reply = e["t_ns"]

    def ms(t: int | None) -> float | None:
        return round((t - asked) / 1e6) if t is not None and asked is not None else None

    return ms(first_audio), ms(last_reply)


def eval_agent(folder: str, web: bool) -> tuple[Agent | None, dict[str, str]]:
    """A document agent scoped to the corpus folder, or with ``web`` the generalist with docs and
    the knowledge server's web search, since a document agent cannot leave its documents (ADR-039)."""
    if web:
        return None, {"docs": TASK_SERVERS["docs"], "knowledge": TASK_SERVERS["knowledge"]}
    agent = Agent(id="me4_eval", name="ME4 eval", kind="documents", model="direct", color="#7ee0a1",
                  documents=[folder], builtin=False)  # fmt: skip
    return agent, {"docs": TASK_SERVERS["docs"]}


async def ask_all(cfg, questions: list[dict], folder: str, web: bool, out: Path) -> list[dict]:
    rows = []
    for i, q in enumerate(questions, 1):
        agent, servers = eval_agent(folder, web or q["needs_web"])
        rec: dict = {"id": q["id"], "question": q["question"]}
        t0 = time.monotonic()
        try:
            r = await run_bot(cfg, input_mode="text", turns=[q["question"]], data_dir=str(EVAL_DIR),
                              handle_sigint=False, servers=servers, agent=agent, reply_timeout_s=90.0)  # fmt: skip
        except Exception as e:  # noqa: BLE001, a failed provider is a result here
            rec["error"] = f"{type(e).__name__}: {e}"
        else:
            rec |= {"run_id": r.run_id, "reply": r.spoken_text or r.llm_text, "tool_calls": r.tool_calls,
                    "usd": r.session_usd}  # fmt: skip
            rec["first_audio_ms"], rec["reply_ms"] = timings(r.events_path)
        rec["wall_s"] = round(time.monotonic() - t0, 1)
        with out.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
        row = score(q, rec)
        rows.append(row)
        mark = "ok " if row["answer"] else "MISS"
        print(f"[{i}/{len(questions)}] {mark} {q['id']}: {rec.get('reply', rec.get('error', ''))[:100]!r}")
    return rows


def load_questions(paths: list[Path]) -> list[dict]:
    """Questions from the generated JSON and the hand-written YAML, with the optional fields filled."""
    out: list[dict] = []
    for path in paths:
        if not path.exists():
            print(f"skipping {path}: not there (the generated set comes from bench.me4_corpus)")
            continue
        raw = yaml.safe_load(path.read_text()) if path.suffix in {".yaml", ".yml"} else json.loads(path.read_text())
        for q in raw["questions"]:
            out.append({"number": None, "numbers": None, "tolerance": 0.0, "needs_web": False, "refs": [], **q})
    ids = [q["id"] for q in out]
    if len(ids) != len(set(ids)):
        sys.exit("question ids repeat across the question files")
    return out


def missing_files(q: dict, corpus: Path) -> list[str]:
    return [r["file"] for r in q["refs"] if not (corpus / r["file"]).exists()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--questions", type=Path, action="append",
                    help=f"a question file, may repeat (default: {GENERATED.name} and {PUBLIC.name})")  # fmt: skip
    ap.add_argument("--folder", default="me4", help="the corpus folder under data/me4_eval/documents")
    ap.add_argument("--stages", default="", help="only llm may change, e.g. llm=anthropic (needs ME3_LIVE=1)")
    ap.add_argument("--model", help="override the model of the chosen LLM provider")
    ap.add_argument("--kind", help="only questions of this kind, e.g. csv_agg")
    ap.add_argument("--limit", type=int, help="only the first N questions")
    ap.add_argument("--web", action="store_true", help="every question gets web search, not only web ones")
    ap.add_argument("--score", type=Path, help="score an earlier run's answers file instead of asking")
    args = ap.parse_args(argv)

    questions = load_questions(args.questions or [GENERATED, PUBLIC])
    if args.kind:
        questions = [q for q in questions if q["kind"] == args.kind]
    if args.limit:
        questions = questions[: args.limit]

    if args.score:
        recs = {r["id"]: r for r in map(json.loads, args.score.read_text().splitlines())}
        rows = [score(q, recs[q["id"]]) for q in questions if q["id"] in recs]
    else:
        os.environ.setdefault("ME3_TOOL_STAGES", FAKE_TOOL_STAGES)
        cfg = load_config("mac")
        cfg.providers["stages"] = {"stt": "fake", "llm": "fake", "tts": "fake"}
        for item in filter(None, args.stages.split(",")):
            stage, _, choice = item.partition("=")
            if stage != "llm":
                ap.error("the eval varies the LLM only")
            cfg.providers["stages"]["llm"] = choice
        live = cfg.providers["stages"]["llm"] != "fake"
        if live and os.environ.get("ME3_LIVE") != "1":
            ap.error("a real LLM spends money: set ME3_LIVE=1 to confirm")
        if args.model:
            if not live:
                ap.error("--model needs a real LLM")
            cfg.providers["providers"][cfg.providers["stages"]["llm"]]["model"] = args.model
        corpus = EVAL_DIR / "documents" / args.folder
        if not corpus.is_dir():
            ap.error(f"no corpus at {corpus}: run bench.me4_corpus first")
        for q in [q for q in questions if missing_files(q, corpus)]:
            print(f"skipping {q['id']}: {', '.join(missing_files(q, corpus))} not in the corpus")
        questions = [q for q in questions if not missing_files(q, corpus)]
        RUNS.mkdir(parents=True, exist_ok=True)
        out = RUNS / f"{time.strftime('%Y%m%d-%H%M%S')}-{cfg.providers['stages']['llm']}.jsonl"
        print(f"{len(questions)} questions, llm={cfg.providers['stages']['llm']}, answers to {out}")
        rows = asyncio.run(ask_all(cfg, questions, args.folder, args.web, out))

    if not rows:
        print("no answers to score")
        return 1
    print(json.dumps(summary(rows), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
