"""The ME4 practice corpus and its scorer. No LLM, no network."""

from __future__ import annotations

import csv
import json
from collections import Counter

import pytest
from pypdf import PdfReader

from bench import me4_corpus as corpus
from bench import me4_eval as ev


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("me4")
    out, qpath = root / "documents" / "me4", root / "q.json"
    meta = corpus.build(out, seed=7, rows=2000, filler_mb=0.2, questions_path=qpath)
    return out, qpath, meta


def test_corpus_has_every_format(built):
    out, _, meta = built
    kinds = Counter(p.suffix for p in out.rglob("*") if p.is_file())
    for suffix in [".md", ".docx", ".pptx", ".pdf", ".csv", ".xlsx"]:
        assert kinds[suffix] >= 1, suffix
    assert meta["filler_files"] >= 1


def test_same_seed_same_questions(built, tmp_path):
    _, _, meta = built
    again = corpus.build(tmp_path / "me4", seed=7, rows=2000, filler_mb=0, questions_path=tmp_path / "q.json")
    assert again["questions"] == meta["questions"]


def test_questions_are_outside_the_documents(built):
    out, qpath, _ = built
    assert not qpath.is_relative_to(out)


def test_scanned_memo_has_no_text_layer(built):
    out, _, _ = built
    pages = PdfReader(out / "archive" / "signed_lease_memo_scan.pdf").pages
    assert len(pages) == 2 and not "".join(p.extract_text() or "" for p in pages).strip()


def test_chart_numbers_are_not_in_the_slide_text(built):
    from pptx import Presentation

    out, _, meta = built
    slide = Presentation(out / "board" / "fleet_strategy_2027.pptx").slides[3]
    text = " ".join(s.text_frame.text for s in slide.shapes if s.has_text_frame)
    image_qs = [q for q in meta["questions"] if q["kind"] == "pptx_image"]
    assert image_qs and all(q["refs"][0]["slide"] == 4 for q in image_qs)
    assert not any(ch.isdigit() for ch in text)


def test_csv_gold_matches_the_file(built):
    out, _, meta = built
    rows = list(csv.DictReader((out / "data" / "shipments_2026.csv").open()))
    for q in (q for q in meta["questions"] if q["kind"] == "csv_agg" and "delayed" in q["question"]):
        words = q["question"].removeprefix("How many shipments from ").removesuffix(" 2026?")
        hub, month = words.split(" were delayed in ")
        m = corpus.YEAR_MONTHS.index(month) + 1
        n = sum(1 for r in rows if r["origin_hub"] == hub and int(r["date"][5:7]) == m and r["status"] == "delayed")
        assert q["number"] == n


def test_every_question_has_refs_unless_unanswerable(built):
    _, _, meta = built
    for q in meta["questions"]:
        assert bool(q["refs"]) != (q["kind"] == "unanswerable"), q["id"]
    assert len(meta["questions"]) >= 45


@pytest.mark.parametrize("text,value", [
    ("fourteen days", 14), ("two hundred thirty-five thousand", 235_000), ("one point two five times", 1.25),
    ("PHP 1,234,500", 1_234_500), ("about 2.5 million pesos", 2_500_000), ("33.5M", 33_500_000),
])  # fmt: skip
def test_numbers_in(text, value):
    assert value in ev.numbers_in(text)


def q(kind="lookup", answer="14 days", number=14.0, refs=None, tolerance=0.0):
    return {"id": "x", "kind": kind, "answer": answer, "number": number, "tolerance": tolerance,
            "refs": refs if refs is not None else [{"file": "handbook/employee_handbook.md"}]}  # fmt: skip


def test_answer_numbers_and_words():
    assert ev.answer_ok(q(), "Regular employees get fourteen days.")
    assert ev.answer_ok(q(), "You get 14.")
    assert not ev.answer_ok(q(), "You get 12 days, per the 2024 edition.")
    assert ev.answer_ok(q(number=1000.0, tolerance=0.1), "about 1,080 dollars")
    assert not ev.answer_ok(q(number=1000.0, tolerance=0.1), "about 1,200 dollars")
    assert ev.answer_ok(q(answer="March 5, 2027", number=None), "The vote moved to March 5, 2027.")
    assert not ev.answer_ok(q(answer="March 5, 2027", number=None), "Sometime in 2027.")


def test_unanswerable_needs_a_plain_no():
    u = q(kind="unanswerable", number=None, refs=[])
    assert ev.answer_ok(u, "The documents don't mention a dental benefit cap.")
    assert ev.answer_ok(u, "I couldn't find that in the files.")
    assert not ev.answer_ok(u, "The cap is 20,000 pesos.")


def test_citation_needs_the_whole_title_and_the_page():
    pdf = q(refs=[{"file": "safety/incident_review_2026.pdf", "page": 2}])
    row = ev.score(pdf, {"reply": "Two people, per the incident review, page 2."})
    assert row["cited"] and row["cited_file"]
    row = ev.score(pdf, {"reply": "Two people, per the incident review."})
    assert row["cited_file"] and not row["cited"]
    row = ev.score(q(), {"reply": "Regular employees get 14 days."})
    assert not row["cited_file"]  # "employees" alone is not the employee handbook


def test_retrieved_reads_the_tool_calls():
    calls = [{"name": "read_document", "args": {"path": "me4/handbook/employee_handbook.md"}}]
    assert ev.score(q(), {"reply": "", "tool_calls": calls})["retrieved"]
    assert not ev.score(q(), {"reply": "", "tool_calls": []})["retrieved"]


def test_timings_from_events(tmp_path):
    p = tmp_path / "e.jsonl"
    events = [{"event": "user_text", "t_ns": 1_000_000_000}, {"event": "tts_first_byte", "t_ns": 1_800_000_000},
              {"event": "reply_text", "t_ns": 2_500_000_000}]  # fmt: skip
    p.write_text("\n".join(map(json.dumps, events)))
    assert ev.timings(p) == (800, 1500)


def test_summary_by_kind():
    rows = [ev.score(q(), {"reply": "14 days, employee handbook", "reply_ms": 900}),
            ev.score(q(kind="csv_agg"), {"reply": "no idea", "reply_ms": 1500})]  # fmt: skip
    s = ev.summary(rows)
    assert s["all"]["answer"] == 0.5 and s["by_kind"]["lookup"]["cited"] == 1.0


def test_public_questions_load_and_point_at_public_files():
    qs = ev.load_questions([ev.PUBLIC])
    assert len(qs) >= 15
    for x in qs:
        assert x["refs"] and all(r["file"].startswith("public/") and r.get("page") for r in x["refs"]), x["id"]


def test_several_numbers_must_all_appear():
    both = q(number=None, answer="5.3 and 5.0")
    both["numbers"] = [5.3, 5.0]
    assert ev.answer_ok(both, "The World Bank said 5.3 percent; the ADB later said 5.0 percent.")
    assert not ev.answer_ok(both, "The World Bank said 5.3 percent.")


def test_questions_without_their_files_are_skipped(tmp_path):
    (tmp_path / "public").mkdir()
    (tmp_path / "public" / "a.pdf").write_bytes(b"%PDF")
    have = q(refs=[{"file": "public/a.pdf", "page": 1}])
    lack = q(refs=[{"file": "public/b.pdf", "page": 1}])
    assert ev.missing_files(have, tmp_path) == []
    assert ev.missing_files(lack, tmp_path) == ["public/b.pdf"]


def test_repeated_ids_across_files_are_refused(tmp_path):
    a = tmp_path / "a.yaml"
    a.write_text("questions:\n  - {id: x, kind: k, question: q, answer: a}\n")
    with pytest.raises(SystemExit):
        ev.load_questions([a, a])
