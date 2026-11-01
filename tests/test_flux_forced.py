"""ForceEndTurn with Smart Turn, against a fake socket and analyzer (no spend, ADR-019)."""

import json
from types import SimpleNamespace

from websockets.protocol import State

from bench.cost_ledger import CostLedger
from harness.bot import _turn_strategies
from harness.config import load_config
from harness.flux_forced import ForcedEndFluxSTT, ForcedEndTurn, ForcedEndTurnStopStrategy
from harness.providers import build_stt


class Socket:
    state = State.OPEN

    def __init__(self):
        self.sent = []

    async def send(self, message):
        self.sent.append(json.loads(message))


def _forced_stt(tmp_path, monkeypatch, **flux):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-value")
    providers = load_config("mac").providers
    providers["stages"]["stt"] = "deepgram_flux"
    providers["providers"]["deepgram"]["daily_cap_usd"] = 1.0
    providers["providers"]["deepgram"]["flux"].update(flux)
    ledger = CostLedger.from_providers_config(providers, tmp_path / "l.jsonl")
    stt = build_stt(providers, ledger, forced=True)
    stt._websocket = Socket()
    return stt


def _strategy():
    calls = []

    async def force_end():
        calls.append(1)

    analyzer = SimpleNamespace(clear=lambda: None)
    return ForcedEndTurnStopStrategy(turn_analyzer=analyzer, force_end=force_end), calls


def test_flux_cannot_end_or_predict_the_turn(tmp_path, monkeypatch):
    stt = _forced_stt(tmp_path, monkeypatch, eot_threshold=0.6, eager_eot_threshold=0.4)
    assert isinstance(stt, ForcedEndFluxSTT)
    assert stt._settings.eot_threshold == 1.0
    assert stt._settings.eager_eot_threshold is None
    assert not stt.eager_end_of_turn_enabled


async def test_force_sends_only_with_an_open_turn(tmp_path, monkeypatch):
    stt = _forced_stt(tmp_path, monkeypatch)
    assert not await stt.force_end_turn()  # noise: Flux never started a turn
    assert stt._websocket.sent == []
    stt._user_is_speaking = True
    assert await stt.force_end_turn()
    assert stt._websocket.sent == [{"type": "ForceEndTurn"}]


async def test_events_say_whether_it_was_sent(tmp_path, monkeypatch):
    seen = []
    force = ForcedEndTurn(lambda name, **f: seen.append((name, f)))
    force.stt = _forced_stt(tmp_path, monkeypatch)
    await force()
    assert seen == [("force_end_turn", {"sent": False})]


async def test_complete_turn_forces_once():
    s, calls = _strategy()
    await s._maybe_trigger_user_turn_stopped()
    assert calls == []  # analyzer has not said complete
    s._turn_complete = True
    await s._maybe_trigger_user_turn_stopped()
    await s._maybe_trigger_user_turn_stopped()
    assert calls == [1]


async def test_user_resuming_rearms_the_force():
    s, calls = _strategy()
    s._turn_complete = True
    await s._maybe_trigger_user_turn_stopped()
    await s._discard_pending_end_of_turn()  # VAD saw speech again
    s._turn_complete = True
    await s._maybe_trigger_user_turn_stopped()
    assert calls == [1, 1]


async def test_finalized_transcript_needs_no_force():
    s, calls = _strategy()
    s._turn_complete = True
    s._transcript_finalized = True
    s._text = ""  # nothing to trigger on, only the force decision is under test
    await s._maybe_trigger_user_turn_stopped()
    assert calls == []


def test_flux_forced_needs_flux():
    import pytest

    cfg = load_config("mac")
    cfg.profile["turn"]["stop"] = "flux_forced"
    with pytest.raises(ValueError, match="needs stages.stt"):
        _turn_strategies(cfg)


def test_duck_mode_keeps_the_interrupt_off():
    cfg = load_config("mac")
    cfg.providers["stages"]["stt"] = "deepgram_flux"
    cfg.profile["turn"]["stop"] = "flux_forced"
    cfg.profile["barge_in"]["mode"] = "duck"
    strategies = _turn_strategies(cfg, ForcedEndTurn())
    assert isinstance(strategies.stop[0], ForcedEndTurnStopStrategy)
    assert not strategies.start[0]._enable_interruptions
