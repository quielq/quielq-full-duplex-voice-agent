"""Flux settings, eager end-of-turn logging, and metering of withdrawn calls (no spend)."""

import json

import pytest
from pipecat.frames.frames import EagerEndOfTurnCancelFrame, EagerTranscriptionFrame, TranscriptionFrame
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from bench.cost_ledger import CostLedger
from harness.config import load_config
from harness.costs import WITHDRAWN_PROMPT_TOKENS, CostObserver
from harness.events import EventLog, TurnEventObserver
from harness.providers import build_stt


def _pushed(frame) -> FramePushed:
    return FramePushed(
        source=FrameProcessor(), destination=FrameProcessor(), frame=frame,
        direction=FrameDirection.DOWNSTREAM, timestamp=0,
    )  # fmt: skip


def _flux(tmp_path, monkeypatch, **flux):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-value")
    providers = load_config("mac").providers
    providers["stages"]["stt"] = "deepgram_flux"
    providers["providers"]["deepgram"]["daily_cap_usd"] = 1.0
    providers["providers"]["deepgram"]["flux"].update(flux)
    ledger = CostLedger.from_providers_config(providers, tmp_path / "l.jsonl")
    return build_stt(providers, ledger)


def test_flux_defaults_stay_untouched(tmp_path, monkeypatch):
    stt = _flux(tmp_path, monkeypatch)
    assert not stt.eager_end_of_turn_enabled
    assert stt._settings.eot_threshold is None
    assert stt._settings.eager_eot_threshold is None
    assert stt._settings.eot_timeout_ms is None


def test_flux_thresholds_reach_the_service(tmp_path, monkeypatch):
    stt = _flux(tmp_path, monkeypatch, eot_threshold=0.6, eot_timeout_ms=2000)
    assert stt._settings.eot_threshold == 0.6
    assert stt._settings.eot_timeout_ms == 2000
    assert not stt.eager_end_of_turn_enabled


def test_an_eager_threshold_turns_eager_on(tmp_path, monkeypatch):
    stt = _flux(tmp_path, monkeypatch, eager_eot_threshold=0.4)
    assert stt.eager_end_of_turn_enabled
    assert stt._settings.eager_eot_threshold == 0.4


async def test_eager_events_and_end_trigger_are_logged(tmp_path):
    log = EventLog(tmp_path)
    obs = TurnEventObserver(log)
    await obs.on_push_frame(_pushed(EagerTranscriptionFrame("set a timer", "u", "t")))
    await obs.on_push_frame(_pushed(EagerEndOfTurnCancelFrame()))
    await obs.on_push_frame(
        _pushed(TranscriptionFrame("set a timer", "u", "t", result={"trigger": "timeout"}))
    )
    log.close()
    events = [json.loads(line) for line in log.path.read_text().splitlines()]
    assert [e["event"] for e in events] == ["eager_eot", "eager_cancel", "asr_final"]
    assert events[2]["trigger"] == "timeout"


async def _withdraw(tmp_path, llm, frames):
    providers = load_config("mac").providers
    providers["stages"] = {"stt": "fake", "llm": llm, "tts": "fake"}
    ledger = CostLedger(tmp_path / "l.jsonl", {})
    obs = CostObserver(ledger, providers, "r")
    for frame in frames:
        await obs.on_push_frame(_pushed(frame))
    return obs


async def test_withdrawn_call_is_metered_once(tmp_path):
    frame = EagerEndOfTurnCancelFrame()
    sibling = EagerEndOfTurnCancelFrame()
    sibling.broadcast_sibling_id = frame.id
    frame.broadcast_sibling_id = sibling.id
    obs = await _withdraw(tmp_path, "groq", [frame, sibling])
    assert obs.session_usd == pytest.approx(WITHDRAWN_PROMPT_TOKENS * 0.000000075)


async def test_withdrawn_call_on_a_fake_llm_is_free(tmp_path):
    obs = await _withdraw(tmp_path, "fake", [EagerEndOfTurnCancelFrame()])
    assert obs.session_usd == 0
