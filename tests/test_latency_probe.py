"""The latency probe (ADR-036) on synthetic event logs, and one short wav run on fakes. No spend."""

import json
import shutil

import pytest

from bench import latency_bench
from bench.latency_probe import build_report, format_table, main, measure_turns, percentiles, stage_deltas
from harness.config import load_config
from harness.mcp_tools import TASK_SERVERS

MS = 1_000_000


def ev(event, t_ms, turn=0, run="r1", **fields):
    return {"run_id": run, "turn_id": turn, "event": event, "t_ns": int(t_ms * MS), **fields}


def audio(event, t_ms, audio_ms, turn=0, run="r1", **fields):
    return ev(event, t_ms, turn, run, audio_t_ns=int(audio_ms * MS), **fields)


def turn(n, base, *, latency=500, tool=False, filler_at=None, run="r1"):
    """One user turn: speech ends at base, end of turn 300 ms later, reply audio at base + latency."""
    out = [
        audio("user_audio_onset", base - 1000, base - 1200, n - 1, run),
        ev("user_turn_start", base - 1100, n, run),
        ev("injection_speech_end", base + 40, n, run),
        audio("user_audio_end", base + 320, base, n, run),  # logged after the gap, stamped before it
        ev("end_of_turn", base + 300, n, run),
        ev("llm_request", base + 310, n, run),
    ]
    if tool:
        out += [ev("tool_call_start", base + 320, n, run, name="set_timer"), ev("tool_call_end", base + 360, n, run)]
    if filler_at is not None:
        out += [ev("filler_trigger", base + filler_at - 5, n, run),
                audio("local_audio_onset", base + filler_at - 5, base + filler_at, n, run)]  # fmt: skip
    out += [
        ev("llm_first_token", base + latency - 100, n, run),
        audio("reply_audio_onset", base + latency - 2, base + latency, n, run, boundary=False),
        ev("first_audio_out", base + latency - 1, n, run),
    ]
    return out


def session(latencies, run="r1", tools=()):
    events = [ev("run_start", 0, run=run, stages={"stt": "fake", "llm": "fake", "tts": "fake"}, input="wav")]
    for i, lat in enumerate(latencies, 1):
        events += turn(i, i * 10_000, latency=lat, tool=i in tools, run=run)
    events.append(ev("run_end", len(latencies) * 10_000 + 5000, len(latencies), run, session_usd=0.01))
    return events


def write(tmp_path, events, name):
    path = tmp_path / f"events-{name}.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in events))
    return path


def test_headline_is_speech_end_to_reply_audio_from_the_marks():
    rows = measure_turns(session([800, 600, 700]), warmup=0)
    assert [r.latency_ms for r in rows] == [800, 600, 700]
    assert rows[0].stages["end_of_turn_wait"] == 300
    assert rows[0].stages["end_of_turn_to_reply_audio"] == 500


def test_the_thinking_phrase_never_counts_as_reply_audio():
    events = turn(1, 10_000, latency=2500, filler_at=1600)
    (row,) = measure_turns(events, warmup=0)
    assert row.latency_ms == 2500 and row.filler and row.filler_ms == 1600


def test_warmup_drops_the_first_three_turns_of_each_session():
    rows = measure_turns(session([900, 900, 900, 400, 500]))
    assert [r.warmup for r in rows] == [True, True, True, False, False]


def test_unmeasured_turns_say_why():
    no_reply = [e for e in turn(1, 10_000) if e["event"] != "reply_audio_onset"]
    old_log = [e for e in turn(1, 10_000) if "audio_t_ns" not in e]
    assert measure_turns(no_reply, 0)[0].unmeasured == "no_reply"
    assert measure_turns(old_log, 0)[0].unmeasured == "no_speech_end"


def test_a_barge_in_closes_the_turn_window():
    events = turn(1, 10_000, latency=4000)
    events = [e for e in events if e["event"] not in ("reply_audio_onset", "first_audio_out")]
    events += turn(2, 12_000, latency=500)  # the user spoke again before any reply audio
    rows = measure_turns(sorted(events, key=lambda e: e["t_ns"]), 0)
    assert rows[0].unmeasured == "no_reply" and rows[1].latency_ms == 500


def test_injections_join_the_turn_they_end():
    deltas = stage_deltas(session([500, 500]))
    assert deltas[1]["end_of_turn_after_speech"] == 260
    assert deltas[2]["end_of_turn_after_speech"] == 260


def test_percentiles_interpolate():
    p = percentiles(list(range(1, 101)))
    assert (p["n"], p["p50"], p["p90"], p["p95"]) == (100, 50.5, 90.1, 95.0)  # rounded to 0.1 ms
    assert percentiles([])["p50"] is None


def test_report_groups_runs_and_joins_the_ledger(tmp_path):
    a = write(tmp_path, session([900] * 3 + [400, 600], run="a", tools={4}), "a")
    b = write(tmp_path, session([900] * 3 + [500, 700], run="b", tools={5}), "b")
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps({"run_id": "a", "usd": 0.02, "provider": "x"}) + "\n"
                      + json.dumps({"run_id": "other", "usd": 5.0, "provider": "x"}) + "\n")  # fmt: skip
    r = build_report([a, b], ledger=ledger)
    assert (r["turns_total"], r["turns_kept"], r["turns_measured"]) == (10, 4, 4)
    assert r["headline_ms"]["all"]["p50"] == 550
    assert r["headline_ms"]["tool"]["n"] == 2 and r["headline_ms"]["no_tool"]["p50"] == 550
    # Run a from the ledger, run b from its run_end line.
    assert r["cost"]["usd"] == pytest.approx(0.03) and r["cost"]["sources"] == {"ledger": 1, "run_end": 1}
    assert r["cost"]["usd_per_turn"] == pytest.approx(0.003)
    table = format_table(r)
    assert "p95" in table and "no_tool" in table and "per turn" in table


def test_main_takes_run_ids_and_writes_json(tmp_path, capsys):
    write(tmp_path, session([500] * 4, run="20261104-aaa"), "20261104-aaa")
    write(tmp_path, session([600] * 4, run="20261104-bbb"), "20261104-bbb")
    ids = tmp_path / "ids.txt"
    ids.write_text("bbb\n")
    main(["aaa", "--from", str(ids), "--logs-dir", str(tmp_path), "--ledger", str(tmp_path / "none")])
    out = capsys.readouterr().out
    assert "2 runs" in out
    (report,) = tmp_path.glob("latency-*.json")
    assert json.loads(report.read_text())["turns_measured"] == 2


def test_live_stages_refuse_without_me3_live(monkeypatch):
    monkeypatch.delenv("ME3_LIVE", raising=False)
    cfg = load_config("mac")
    with pytest.raises(SystemExit, match="ME3_LIVE=1"):
        latency_bench.apply_stages(cfg, "llm=anthropic")
    assert latency_bench.apply_stages(load_config("mac"), "tts=fake_words")["tts"] == "fake_words"


@pytest.mark.skipif(not shutil.which("say"), reason="macOS `say` not available")
async def test_short_wav_run_on_fakes(tmp_path):
    cfg = load_config("mac")
    cfg.providers["stages"] = {"stt": "fake", "llm": "fake", "tts": "fake"}
    cfg.profile["paths"] = {"logs_dir": str(tmp_path / "logs"), "data_dir": str(tmp_path / "data")}
    paths = await latency_bench.run_bench(cfg, sessions=1, turns=2, gap_s=0.5,
                                          servers={"clock": TASK_SERVERS["clock"]}, data_dir=tmp_path / "s")  # fmt: skip
    r = build_report(paths, warmup=0, ledger=tmp_path / "none")
    assert r["turns_measured"] == 2
    assert r["headline_ms"]["tool"]["n"] == 1 and r["headline_ms"]["no_tool"]["n"] == 1
    # Fakes answer at once: the end-of-turn wait is most of it, and all of it is under 1.5 s.
    assert 0 < r["headline_ms"]["all"]["max"] < 1500
    assert r["attribution_ms"]["end_of_turn_wait"]["n"] == 2
