"""Spoken recovery when the LLM fails and when a spending cap is hit, on fakes."""

import asyncio

import pytest

from harness.bot import run_bot
from harness.config import load_config
from harness.fakes import RuleBasedLLMService
from harness.mcp_tools import TASK_SERVERS
from harness.providers import build_tts


@pytest.fixture
def cfg(tmp_path):
    cfg = load_config("mac")
    cfg.providers["stages"] = {"stt": "fake", "llm": "fake", "tts": "fake"}
    cfg.profile["paths"] = {"logs_dir": str(tmp_path / "logs"), "data_dir": str(tmp_path / "data")}
    return cfg


class RejectingLLM(RuleBasedLLMService):
    """Fails like Groq's schema validation: an ErrorFrame and no reply."""

    async def _respond(self, context) -> None:
        await self.push_error(error_msg="Tool call validation failed")


def run(cfg, tmp_path, **kwargs):
    return asyncio.run(
        run_bot(cfg, input_mode="text", data_dir=str(tmp_path / "data"), reply_timeout_s=5,
                handle_sigint=False, servers={"clock": TASK_SERVERS["clock"]}, **kwargs)  # fmt: skip
    )


def events(result):
    import json

    return [json.loads(line) for line in open(result.events_path)]


def test_a_failed_llm_call_is_spoken_once(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr("harness.bot.build_llm", lambda *a, **k: RejectingLLM())
    result = run(cfg, tmp_path, say="set a timer for zero seconds")
    assert cfg.profile["recovery"]["llm_error_phrase"] in result.spoken_text
    names = [e["event"] for e in events(result)]
    assert names.count("llm_error_spoken") == 1
    assert "reply_timeout" not in names




def test_the_cap_notice_is_spoken_when_tts_is_not_the_capped_provider(cfg, tmp_path, monkeypatch):
    from harness import costs

    async def over(self, provider, kind, units, usd):
        await self._on_cap_reached("groq")

    monkeypatch.setattr(costs.CostObserver, "flush_tts", lambda self: over(self, "groq", "x", 0, 0))
    result = run(cfg, tmp_path, say="hello")
    assert [e for e in events(result) if e["event"] == "spend_cap_reached"]
    assert cfg.profile["recovery"]["cap_phrase"] in result.spoken_text


def test_the_cap_notice_never_spends_on_the_capped_tts(cfg, tmp_path, monkeypatch):
    from harness import costs

    async def over(self):
        await self._on_cap_reached("cartesia")

    monkeypatch.setattr(costs.CostObserver, "flush_tts", over)
    monkeypatch.setattr("harness.bot.build_tts", lambda *a, **k: build_tts({**cfg.providers, "stages": {"tts": "fake"}}, a[1], **k))
    cfg.providers["stages"]["tts"] = "cartesia"
    result = run(cfg, tmp_path, say="hello")
    assert cfg.profile["recovery"]["cap_phrase"] not in result.spoken_text
