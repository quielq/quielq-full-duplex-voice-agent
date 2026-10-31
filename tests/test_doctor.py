"""bench/doctor: the checks that can fail on a bad setup, driven by bad setups, no spend."""

import json

import pytest

from bench import doctor
from bench.doctor import Result, _shape_drift, banned_uses, check_config, check_env, check_spend, run_checks
from harness.config import load_config


@pytest.fixture
def cfg(tmp_path):
    cfg = load_config("mac")
    cfg.providers["stages"] = {"stt": "fake", "llm": "fake", "tts": "fake"}
    cfg.profile["paths"] = {"logs_dir": str(tmp_path / "logs"), "data_dir": str(tmp_path / "data")}
    return cfg


def statuses(rows):
    return {name: status for status, name, *_ in rows}


@pytest.mark.parametrize(
    "source,bad",
    [
        ("x = Foo(allow_interruptions=True)\n", True),
        ("x = Foo(allow_interruptions = True)\n", True),
        ("import ten_vad\n", True),
        ("await client.register_tools(llm)\n", True),
        ("p = LocalTransportParams(a=1, vad_analyzer=v)\n", True),
        ('"""Never allow_interruptions=True or TransportParams(vad_analyzer=x)."""\n', False),
        ("# no .register_tools( here\n", False),
        ("x = 1\n", False),
    ],
)
def test_banned_code_ignores_docs_about_it(source, bad):
    assert bool(banned_uses(source)) is bad


def test_a_selected_provider_without_its_key_fails(cfg, monkeypatch):
    cfg.providers["stages"]["llm"] = "groq"
    monkeypatch.setenv("GROQ_API_KEY", "")
    assert statuses(check_env(cfg))["GROQ_API_KEY"] == "fail"


def test_no_result_ever_carries_a_key_value(cfg, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "secret-value-123")
    assert "secret-value-123" not in json.dumps(list(check_env(cfg)))


def test_a_selected_provider_with_a_zero_cap_fails(cfg):
    cfg.providers["stages"]["llm"] = "anthropic"
    assert statuses(check_config(cfg))["stage llm"] == "fail"


def test_a_real_default_stage_warns_because_a_plain_run_spends(cfg):
    cfg.providers["stages"]["tts"] = "deepgram_aura2"
    assert statuses(check_config(cfg))["stages"] == "warn"


def test_an_unquoted_no_in_a_word_list_fails(cfg):
    cfg.tool_policy["undo_words"] = ["stop", False]
    assert statuses(check_config(cfg))["word lists"] == "fail"


def test_shape_drift_is_found_at_depth():
    assert _shape_drift({"a": {"b": 1}}, {"a": {"c": 1}}) == {"a.b", "a.c"}


def test_spend_at_the_cap_fails_and_near_it_warns(cfg):
    path = cfg.path("data_dir") / "cost_ledger.jsonl"
    from bench.cost_ledger import CostLedger

    ledger = CostLedger(path, {"groq": 0.5, "deepgram": 0.5})
    ledger.record("groq", "x", 1, 0.5)
    ledger.record("deepgram", "x", 1, 0.45)
    rows = statuses(check_spend(cfg))
    assert rows["groq"] == "fail" and rows["deepgram"] == "warn"


def test_a_corrupt_ledger_fails(cfg):
    (cfg.path("data_dir") / "cost_ledger.jsonl").write_text("{not json\n")
    assert statuses(check_spend(cfg))["ledger"] == "fail"


def test_a_crashing_check_is_reported_and_the_rest_still_run(cfg, monkeypatch):
    def boom(_cfg):
        raise ValueError("broken")
        yield

    monkeypatch.setitem(doctor.CHECKS, "env", (boom, "fast"))
    results = run_checks(cfg, ["env", "config"])
    assert results[0] == Result("env", "check crashed", "fail", "ValueError: broken")
    assert any(r.section == "config" for r in results)


def test_slow_sections_pass_on_this_repo(cfg):
    results = run_checks(cfg, ["mcp", "turn"])
    assert [r for r in results if r.status == "fail"] == []
