"""The class benchmark entry (bench/fdb_adapter.py, ADR-044): settings from the environment, fakes by default."""

import asyncio

import pytest

from bench import fdb_adapter
from harness.bot import run_bot
from harness.config import load_config


def test_defaults_stay_fake_and_drop_echo_cancel(monkeypatch):
    monkeypatch.delenv("ME3_STAGES", raising=False)
    monkeypatch.setenv("ME3_PROFILE", "pi")
    cfg = fdb_adapter._config()
    assert cfg.profile["audio"]["echo_cancel"] is False
    assert cfg.profile["ui"]["enabled"] is False
    assert cfg.providers["stages"] == load_config("pi").providers["stages"]


def test_stage_overrides(monkeypatch):
    monkeypatch.setenv("ME3_STAGES", "llm=anthropic")
    assert fdb_adapter._config().providers["stages"]["llm"] == "anthropic"
    monkeypatch.setenv("ME3_STAGES", "nope=x")
    with pytest.raises(SystemExit):
        fdb_adapter._config()


def test_input_is_16k(monkeypatch):
    for profile in ("mac", "pi"):
        monkeypatch.setenv("ME3_PROFILE", profile)
        assert fdb_adapter.audio_rates()[0] == 16000


def test_fdbench_mode_needs_a_transport(tmp_path):
    cfg = load_config("mac")
    cfg.profile["ui"]["enabled"] = False
    with pytest.raises(ValueError, match="needs a transport"):
        asyncio.run(run_bot(cfg, input_mode="fdbench", servers={}, data_dir=str(tmp_path), handle_sigint=False))
