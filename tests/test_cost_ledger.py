from datetime import date

import pytest

from bench.cost_ledger import CostLedger, SpendRefused


def test_zero_cap_refuses(tmp_path):
    ledger = CostLedger(tmp_path / "l.jsonl", {"groq": 0.0})
    with pytest.raises(SpendRefused, match="cap is zero"):
        ledger.authorize("groq")


def test_unknown_provider_refused(tmp_path):
    ledger = CostLedger(tmp_path / "l.jsonl", {})
    with pytest.raises(SpendRefused):
        ledger.authorize("openrouter")


def test_cap_reached_refuses(tmp_path):
    ledger = CostLedger(tmp_path / "l.jsonl", {"groq": 0.01})
    ledger.authorize("groq")
    ledger.record("groq", "llm_output_tokens", 40_000, 0.012)
    assert ledger.over_cap("groq")
    with pytest.raises(SpendRefused, match="reached"):
        ledger.authorize("groq")


def test_spend_is_per_day_and_per_provider(tmp_path):
    day = [date(2026, 10, 21)]
    ledger = CostLedger(tmp_path / "l.jsonl", {"groq": 1.0, "deepgram": 1.0}, today=lambda: day[0])
    ledger.record("groq", "x", 1, 0.25)
    ledger.record("deepgram", "x", 1, 0.5)
    assert ledger.spent_today("groq") == pytest.approx(0.25)
    day[0] = date(2026, 10, 22)
    assert ledger.spent_today("groq") == 0.0
    assert ledger.remaining("deepgram") == pytest.approx(1.0)


def test_spent_total_spans_days_and_providers(tmp_path):
    day = [date(2026, 10, 21)]
    ledger = CostLedger(tmp_path / "l.jsonl", {"groq": 1.0}, today=lambda: day[0])
    ledger.record("groq", "x", 1, 0.25)
    day[0] = date(2026, 10, 22)
    ledger.record("deepgram", "x", 1, 0.5)
    assert ledger.spent_total() == 0.75


def _llm_usd(tmp_path, model):
    from pipecat.metrics.metrics import LLMTokenUsage, LLMUsageMetricsData

    from harness.config import load_config
    from harness.costs import CostObserver

    providers = load_config("mac").providers
    providers["stages"] = {"stt": "fake", "llm": "groq", "tts": "fake"}
    providers["providers"]["groq"]["model"] = model
    obs = CostObserver(CostLedger(tmp_path / "l.jsonl", {}), providers, "r")
    usage = LLMTokenUsage(prompt_tokens=1_000_000, completion_tokens=1_000_000, total_tokens=2_000_000)
    return sum(row[3] for row in obs._price(LLMUsageMetricsData(processor="p", value=usage)))


def test_groq_is_priced_by_model(tmp_path):
    assert _llm_usd(tmp_path, "openai/gpt-oss-20b") == pytest.approx(0.375)
    assert _llm_usd(tmp_path, "openai/gpt-oss-120b") == pytest.approx(0.75)
