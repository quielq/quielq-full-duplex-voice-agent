"""Gate 1, scripted: a timer request reaches the FastMCP clock tool through Pipecat's
MCPClient over stdio and over streamable HTTP, and the result is spoken.

All stages are fakes, so this costs nothing. The wav cases run real Silero VAD and
Smart Turn on speech synthesised by macOS `say` (skipped where `say` is missing).
"""

import json
import shutil
import subprocess

import pytest

from harness.bot import run_bot
from harness.config import load_config
from harness.providers import build_llm, build_stt, build_tts

from bench.cost_ledger import CostLedger, SpendRefused


@pytest.fixture
def cfg(tmp_path):
    cfg = load_config("mac")
    cfg.providers["stages"] = {"stt": "fake", "llm": "fake", "tts": "fake"}
    cfg.profile["paths"] = {"logs_dir": str(tmp_path / "logs"), "data_dir": str(tmp_path / "data")}
    return cfg


def _timers(data_dir):
    return list(json.loads((data_dir / "clock.json").read_text()).values())


@pytest.mark.parametrize("transport", ["stdio", "http"])
async def test_text_request_sets_timer(cfg, tmp_path, transport):
    data = tmp_path / "data"
    r = await run_bot(cfg, input_mode="text", mcp_transport=transport,
                      data_dir=str(data), handle_sigint=False, reply_timeout_s=30)  # fmt: skip
    assert [c["name"] for c in r.tool_calls] == ["set_timer"]
    assert r.tool_calls[0]["args"]["duration_seconds"] == 60
    assert r.spoken_text.endswith("Timer set for one minute.")
    timers = _timers(data)
    assert len(timers) == 1 and timers[0]["kind"] == "timer"
    assert r.session_usd == 0.0


@pytest.fixture(scope="module")
def spoken_request(tmp_path_factory):
    if not shutil.which("say"):
        pytest.skip("macOS `say` not available")
    path = tmp_path_factory.mktemp("audio") / "request.wav"
    subprocess.run(
        ["say", "-o", str(path), "--data-format=LEI16@16000", "Set a timer for one minute."],
        check=True,
    )
    return path


@pytest.mark.parametrize("transport", ["stdio", "http"])
async def test_spoken_request_sets_timer(cfg, tmp_path, spoken_request, transport):
    data = tmp_path / "data"
    r = await run_bot(cfg, input_mode="wav", wav=spoken_request, mcp_transport=transport,
                      data_dir=str(data), handle_sigint=False, reply_timeout_s=30)  # fmt: skip
    assert r.transcripts == ["set a timer for one minute"]
    assert [c["name"] for c in r.tool_calls] == ["set_timer"]
    assert r.spoken_text.endswith("Timer set for one minute.")
    events = [json.loads(line)["event"] for line in r.events_path.read_text().splitlines()]
    for e in ("vad_onset", "vad_end", "end_of_turn", "tool_call_start", "tool_call_end", "tts_first_byte"):
        assert e in events
    assert len(_timers(data)) == 1


def test_real_providers_refused_at_zero_cap(cfg, tmp_path):
    ledger = CostLedger(tmp_path / "l.jsonl", {"groq": 0.0, "deepgram": 0.0})
    cfg.providers["stages"] = {"stt": "deepgram_flux", "llm": "groq", "tts": "deepgram_aura2"}
    for build in (build_stt, build_llm, build_tts):
        with pytest.raises(SpendRefused):
            build(cfg.providers, ledger)
