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


def test_ab_switches(monkeypatch):
    monkeypatch.setenv("ME3_PROMPT_CACHE", "0")
    monkeypatch.setenv("ME3_ADDRESSING", "1")
    monkeypatch.setenv("ME3_BARGE_IN", "duck")
    monkeypatch.setenv("ME3_FLUX", "eager_eot_threshold=off")
    cfg = fdb_adapter._config()
    assert cfg.providers["providers"]["anthropic"]["prompt_caching"] is False
    assert cfg.profile["addressing"]["enabled"] is True
    assert cfg.profile["addressing"]["gate"]["enabled"] is False
    assert cfg.profile["barge_in"]["mode"] == "duck"
    assert cfg.providers["providers"]["deepgram"]["flux"]["eager_eot_threshold"] is None


def test_unset_switches_keep_the_profile(monkeypatch):
    for k in ("ME3_PROMPT_CACHE", "ME3_ADDRESSING", "ME3_BARGE_IN", "ME3_FLUX"):
        monkeypatch.delenv(k, raising=False)
    cfg = fdb_adapter._config()
    assert cfg.providers["providers"]["anthropic"]["prompt_caching"] is True  # on by default (ADR-045)
    assert cfg.profile["addressing"]["enabled"] is False
    assert cfg.profile["barge_in"]["mode"] == "immediate"


def test_bad_switch_values_refuse(monkeypatch):
    monkeypatch.setenv("ME3_PROMPT_CACHE", "yes")
    with pytest.raises(SystemExit):
        fdb_adapter._config()
    monkeypatch.setenv("ME3_PROMPT_CACHE", "1")
    monkeypatch.setenv("ME3_BARGE_IN", "sometimes")
    with pytest.raises(SystemExit):
        fdb_adapter._config()
    monkeypatch.setenv("ME3_BARGE_IN", "duck")
    monkeypatch.setenv("ME3_FLUX", "speed=11")
    with pytest.raises(SystemExit):
        fdb_adapter._config()


def test_describe_names_the_models(monkeypatch):
    from bench.fdb_adapter import describe

    monkeypatch.setenv("ME3_STAGES", "stt=voice_listen,llm=anthropic,tts=voice_speak")
    d = describe()
    assert d["LLM"] == "claude-haiku-5-5"
    assert "flux-general-en" in d["ASR"] and "voice-listen" in d["ASR"]
    assert "sonic-3.6" in d["TTS"] and d["Prompt"].startswith("VIA v")
