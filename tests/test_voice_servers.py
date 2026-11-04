"""The voice servers (ADR-032) against fake Flux and Cartesia upstreams on loopback.

Nothing here reaches a provider: each server is pointed at a local fake with its test
variable, and the keys are dummy values. Covers the relay itself, the refusals, both MCP
transports, and a whole bot turn with STT and TTS through the servers.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess

import pytest
import websockets

from bench.voice_fakes import TRANSCRIPT, FakeCartesia, FakeFlux
from harness.bot import run_bot
from harness.config import load_config
from harness.voice_relay import RelayClient
from servers.voice_listen.server import flux_events
from servers.voice_relay import Relay, Upstream
from servers.voice_speak.server import _FirstChunk

DUMMY = "dummy-test-value"


@pytest.fixture
async def upstreams(monkeypatch, tmp_path):
    flux, cartesia = FakeFlux(), FakeCartesia()
    async with (websockets.serve(flux.handler, "127.0.0.1", 0, process_request=flux.process_request) as f,
                websockets.serve(cartesia.handler, "127.0.0.1", 0,
                                 process_request=cartesia.process_request) as c):  # fmt: skip
        fport, cport = f.sockets[0].getsockname()[1], c.sockets[0].getsockname()[1]
        monkeypatch.setenv("ME3_VOICE_LISTEN_UPSTREAM", f"ws://127.0.0.1:{fport}/v2/listen")
        monkeypatch.setenv("ME3_VOICE_SPEAK_UPSTREAM", f"ws://127.0.0.1:{cport}/tts/websocket")
        monkeypatch.setenv("DEEPGRAM_API_KEY", DUMMY)
        monkeypatch.setenv("CARTESIA_API_KEY", DUMMY)
        monkeypatch.setenv("ME3_DATA_DIR", str(tmp_path / "data"))
        yield flux, cartesia


async def _wait_for(events, name, timeout=3.0):
    """Events ride MCP, the audio rides the socket, so an event can land a moment later."""
    async with asyncio.timeout(timeout):
        while name not in [n for n, _ in events]:
            await asyncio.sleep(0.02)


def _recorder():
    events: list[tuple[str, dict]] = []
    return events, lambda name, **data: events.append((name, data))


@pytest.mark.parametrize("transport", ["stdio", "http"])
async def test_listen_relays_audio_and_turns(upstreams, tmp_path, transport):
    flux, _ = upstreams
    events, log = _recorder()
    client = RelayClient("listen", transport, str(tmp_path / "data"), log)
    try:
        ws = await client.open("listen", {"query": "model=flux-general-en&sample_rate=16000&encoding=linear16"})
        assert json.loads(await ws.recv())["type"] == "Connected"
        await ws.send(b"\x00" * 32000)
        turns = [json.loads(await ws.recv()) for _ in range(2)]
        assert [t["event"] for t in turns] == ["StartOfTurn", "EndOfTurn"]
        await _wait_for(events, "voice_transcript_final")
        await ws.close()
    finally:
        await client.close()
    assert flux.audio == 32000
    assert flux.seen[0]["path"] == "/v2/listen?model=flux-general-en&sample_rate=16000&encoding=linear16"
    assert flux.seen[0]["auth"] == f"Token {DUMMY}"
    names = [name for name, _ in events]
    assert {"voice_stream_ready", "voice_upstream_connected", "voice_speech_started", "voice_transcript_final"} <= set(names)
    order = [n for n in names if n != "voice_stream_closed"]
    assert order.index("voice_speech_started") < order.index("voice_transcript_final")  # queued, still in order
    assert names[-1] == "voice_stream_closed"
    final = next(d for n, d in events if n == "voice_transcript_final")
    assert final["text"] == TRANSCRIPT
    logged = json.dumps(events)
    assert DUMMY not in logged and "token" not in logged and ".sock" not in logged


async def test_speak_sends_version_and_names_replies(upstreams, tmp_path):
    _, cartesia = upstreams
    events, log = _recorder()
    client = RelayClient("speak", "stdio", str(tmp_path / "data"), log)
    try:
        ws = await client.open("speak", {"query": "cartesia_version=2025-04-16", "version": "2025-04-16"})
        await ws.send(json.dumps({"transcript": "Hello there. ", "context_id": "c1", "continue": True,
                                  "output_format": {"sample_rate": 24000}}))  # fmt: skip
        kinds = [json.loads(await ws.recv())["type"] for _ in range(2)]
        await ws.send(json.dumps({"transcript": "", "context_id": "c1", "continue": False}))
        kinds.append(json.loads(await ws.recv())["type"])
        await _wait_for(events, "voice_audio_done")
        await ws.close()
    finally:
        await client.close()
    assert kinds == ["chunk", "timestamps", "done"]
    assert cartesia.seen[0] == {"path": "/tts/websocket?cartesia_version=2025-04-16", "key": DUMMY, "version": "2025-04-16"}
    names = [n for n, _ in events]
    assert names.count("voice_audio_started") == 1 and "voice_audio_done" in names
    assert DUMMY not in json.dumps(events)


async def test_missing_key_refuses(upstreams, tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_KEY")
    events, log = _recorder()
    client = RelayClient("listen", "stdio", str(tmp_path / "data"), log)
    try:
        with pytest.raises(ConnectionError, match="DEEPGRAM_API_KEY is not set"):
            await client.open("listen", {"query": ""})
    finally:
        await client.close()
    assert [n for n, _ in events] == ["voice_stream_refused"]


async def test_upstream_override_must_be_loopback(upstreams, tmp_path, monkeypatch):
    monkeypatch.setenv("ME3_VOICE_SPEAK_UPSTREAM", "wss://example.com/tts/websocket")
    client = RelayClient("speak", "stdio", str(tmp_path / "data"))
    try:
        with pytest.raises(ConnectionError, match="may only name this machine"):
            await client.open("speak", {"query": ""})
    finally:
        await client.close()


def test_query_cannot_change_the_path(monkeypatch):
    monkeypatch.delenv("ME3_TEST_UP", raising=False)
    relay = Relay("t", Upstream("deepgram", "wss://api.deepgram.com/v2/listen", "X", lambda k: {}, "ME3_TEST_UP"))
    assert relay._url("a=1") == "wss://api.deepgram.com/v2/listen?a=1"
    for bad in ("../x=1", "a=1#frag"):
        with pytest.raises(ValueError):
            relay._url(bad)


async def test_wrong_token_is_dropped(upstreams, monkeypatch):
    import os

    monkeypatch.setenv("ME3_TEST_KEY", DUMMY)
    monkeypatch.setenv("ME3_TEST_UP", os.environ["ME3_VOICE_LISTEN_UPSTREAM"])
    relay = Relay("t", Upstream("deepgram", "wss://api.deepgram.com/v2/listen", "ME3_TEST_KEY", lambda k: {}, "ME3_TEST_UP"))
    ready: asyncio.Future = asyncio.get_running_loop().create_future()

    class Ctx:
        async def log(self, message, level=None, logger_name=None, extra=None):
            if message.get("event") == "stream_ready" and not ready.done():
                ready.set_result(message)

    import servers.voice_relay as vr

    async def emit(ctx, event, **data):
        await ctx.log({"event": event, **data})

    monkeypatch.setattr(vr, "emit", emit)
    monkeypatch.setattr(vr, "CONNECT_TIMEOUT_S", 1.0)
    call = asyncio.create_task(relay.open(Ctx(), ""))
    info = await ready
    reader, writer = await asyncio.open_unix_connection(info["path"])
    vr.write_frame(writer, "not-the-token")
    await writer.drain()
    assert await reader.read() == b""  # closed on us
    result = await call
    assert result["reason"] == "the harness did not connect"


def test_event_naming():
    assert flux_events({"type": "TurnInfo", "event": "EndOfTurn", "transcript": "hi"}) == ("transcript_final", {"text": "hi"})
    assert flux_events({"type": "TurnInfo", "event": "Update"}) is None
    first = _FirstChunk()
    assert first({"type": "chunk", "context_id": "a"})[0] == "audio_started"
    assert first({"type": "chunk", "context_id": "a"}) is None
    assert first({"type": "done", "context_id": "a"})[0] == "audio_done"


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    if not shutil.which("say"):
        pytest.skip("macOS `say` not available")
    path = tmp_path_factory.mktemp("audio") / "request.wav"
    subprocess.run(["say", "-o", str(path), "--data-format=LEI16@16000", "Set a timer for one minute."], check=True)
    return path


@pytest.mark.parametrize("transport", ["stdio", "http"])
async def test_bot_turn_through_both_servers(upstreams, tmp_path, clip, transport):
    flux, cartesia = upstreams
    flux.quiet_bytes = 16000  # half a second of silence after the clip, as Flux would wait
    cfg = load_config("mac")
    cfg.providers["stages"] = {"stt": "voice_listen", "llm": "fake", "tts": "voice_speak"}
    cfg.providers["providers"]["cartesia"]["voice_id"] = cfg.providers["providers"]["cartesia"].get("voice_id") or "test-voice"
    cfg.profile["paths"] = {"logs_dir": str(tmp_path / "logs"), "data_dir": str(tmp_path / "data")}
    r = await run_bot(cfg, input_mode="wav", wav=clip, mcp_transport=transport, data_dir=str(tmp_path / "data"),
                      handle_sigint=False, reply_timeout_s=30)  # fmt: skip
    events = [json.loads(line) for line in r.events_path.read_text().splitlines()]
    names = [e["event"] for e in events]
    assert flux.audio > 0 and cartesia.requests
    assert "voice_transcript_final" in names and "voice_audio_started" in names
    assert any("timer" in (q.get("transcript") or "").lower() for q in cartesia.requests)
    text = r.events_path.read_text()
    assert DUMMY not in text and ".sock" not in text
