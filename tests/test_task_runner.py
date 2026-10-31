"""ADR-009 task runner on fakes: every case passes, and each failure lands in its bucket."""

import pytest

from bench.task_runner import (
    BUCKETS,
    LAYERS,
    Observed,
    RunResult,
    classify,
    load_cases,
    report,
    run_case,
    run_suite,
)
from harness.config import load_config


@pytest.fixture
def cfg(tmp_path):
    cfg = load_config("mac")
    cfg.providers["stages"] = {"stt": "fake", "llm": "fake", "tts": "fake"}
    cfg.profile["paths"] = {"logs_dir": str(tmp_path / "logs"), "data_dir": str(tmp_path / "data")}
    return cfg


def call(name, **args):
    return {"name": name, "args": args}


@pytest.mark.parametrize(
    "expect,seen,bucket",
    [
        ({"tools": ["a"]}, Observed(tool_calls=[call("a")]), None),
        ({"tools": ["a"]}, Observed(tool_calls=[call("b")]), "wrong_tool"),
        ({"tools": []}, Observed(tool_calls=[call("b")]), "wrong_tool"),
        ({"tools": ["a"]}, Observed(), "missed_tool"),
        ({"tools_include": ["a"]}, Observed(), "missed_tool"),
        ({"tools_include": ["a"]}, Observed(tool_calls=[call("b")]), "wrong_tool"),
        ({"args": {"a": {"x": 1}}}, Observed(tool_calls=[call("a", x=2)]), "bad_args"),
        ({"args": {"a": {"x": {"re": "^7"}}}}, Observed(tool_calls=[call("a", x="07")]), "bad_args"),
        ({"args": {"a": {"x": {"re": "^0"}}}}, Observed(tool_calls=[call("a", x="07")]), None),
        ({"state": {"s": {"count": 1}}}, Observed(state={"s": []}), "wrong_state"),
        ({"state": {"s": {"has": [{"k": 1}]}}}, Observed(state={"s": [{"k": 2}]}), "wrong_state"),
        ({"silent": True}, Observed(spoken="hi"), "spoke_when_silent"),
        ({"silent": True}, Observed(timed_out=True), None),
        ({"tools": []}, Observed(timed_out=True), "timeout"),
        ({"tools": []}, Observed(provider_error="boom", timed_out=True), "provider_error"),
        ({"spoken_re": "yes"}, Observed(spoken="no"), "wrong_state"),
    ],
)
def test_classify(expect, seen, bucket):
    got, _ = classify(expect, seen)
    assert got == bucket
    assert got is None or got in BUCKETS


def test_wrong_tool_is_not_also_blamed_on_state():
    seen = Observed(tool_calls=[call("b")], state={"s": []})
    assert classify({"tools": ["a"], "state": {"s": {"count": 1}}}, seen)[0] == "wrong_tool"


def test_report_counts_cost_per_success():
    rows = [RunResult("a", "tool_call", None, "", 0.02, 1.0),
            RunResult("a", "tool_call", "bad_args", "x", 0.02, 1.0)]  # fmt: skip
    out = report(rows)
    assert out["cases"]["a"]["pass_rate"] == 0.5
    assert out["buckets"] == {"bad_args": 1}
    assert out["usd_per_success"] == 0.04  # the failed run's spend counts against the success


def test_report_with_no_success_has_no_unit_cost():
    assert report([RunResult("a", "tool_call", "timeout", "", 0.0, 1.0)])["usd_per_success"] is None


def test_cases_load_and_cover_every_layer():
    cases = load_cases()
    assert {c.layer for c in cases} == set(LAYERS)


async def test_all_cases_pass_on_fakes(cfg):
    out = await run_suite(cfg, load_cases())
    assert out["failures"] == []
    assert out["overall"]["pass_rate"] == 1.0
    assert out["total_usd"] == 0.0


async def test_repeat_multiplies_runs(cfg):
    case = next(c for c in load_cases() if c.id == "next_no_tool")
    out = await run_suite(cfg, [case], repeat=2)
    assert out["cases"]["next_no_tool"] == {"passed": 2, "runs": 2, "pass_rate": 1.0}


async def test_a_failing_case_reports_its_bucket(cfg):
    case = next(c for c in load_cases() if c.id == "next_no_tool")
    case.expect = {"tools": ["set_timer"]}
    r = await run_case(cfg, case)
    assert r.bucket == "missed_tool"


async def test_refused_provider_is_a_provider_error(cfg):
    cfg.providers["stages"]["llm"] = "groq"
    cfg.providers["providers"]["groq"]["daily_cap_usd"] = 0.0
    r = await run_case(cfg, next(c for c in load_cases() if c.id == "next_no_tool"))
    assert r.bucket == "provider_error"
