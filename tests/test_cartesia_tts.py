"""Cartesia in the harness: hop timing and interruption, through a fake websocket (no spend)."""

import asyncio
import json

from bench.cost_ledger import CostLedger
from harness.audio_hops import AudioHops
from harness.config import load_config
from harness.providers import build_tts
from pipecat.frames.frames import TTSAudioRawFrame


class FakeSocket:
    def __init__(self):
        self.sent = []

    async def send(self, message):
        self.sent.append(message)


def _build(tmp_path, monkeypatch, hops=None):
    monkeypatch.setenv("CARTESIA_API_KEY", "test")
    providers = load_config("mac").providers
    providers["stages"] = {**providers["stages"], "tts": "cartesia"}
    ledger = CostLedger(tmp_path / "l.jsonl", {"cartesia": 1.0})
    return build_tts(providers, ledger, hops=hops)


def test_cartesia_is_requested_at_the_output_rate(tmp_path, monkeypatch):
    config = load_config("mac")
    assert _build(tmp_path, monkeypatch)._init_sample_rate == config.profile["audio"]["out_sample_rate"]


def test_interruption_cancels_the_context_and_clears_the_backlog(tmp_path, monkeypatch):
    events = []
    hops = AudioHops(lambda name, **fields: events.append(fields), window_s=0)
    service = _build(tmp_path, monkeypatch, hops)
    service._websocket = FakeSocket()

    async def run():
        hops.on_arrival(4800, 24000)
        await service.append_to_audio_context(
            "reply", TTSAudioRawFrame(audio=bytes(4800), sample_rate=24000, num_channels=1)
        )
        await service.on_audio_context_interrupted("reply")

    asyncio.run(run())
    assert json.loads(service._websocket.sent[-1]) == {"context_id": "reply", "cancel": True}
    assert hops._backlog_ms == 0
