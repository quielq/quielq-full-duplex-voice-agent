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
