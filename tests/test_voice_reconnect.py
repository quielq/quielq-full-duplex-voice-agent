"""Recovery for the voice servers (ADR-032) when a stream drops, is refused, or sits idle.

Fakes only: the upstreams in ``bench/voice_fakes.py`` on loopback and dummy keys. Each case
runs for voice-listen (Flux) and voice-speak (Cartesia) over stdio and HTTP:

1. the provider closes the stream mid-stream,
2. the provider refuses the reconnect once, then accepts,
3. the voice server process dies, and ``RelayClient`` starts it again,
4. the speak stream sits idle until the provider times it out,
5. a stop, or the bot shutting down, while a stream is still connecting.

The last test is a whole bot run where both upstreams drop between two turns.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import time

import pytest

from harness.bot import run_bot
from harness.config import load_config
from harness.file_input import Segment
from harness.voice_relay import RelayClient
from tests.test_voice_servers import DUMMY, _recorder, _wait_for, clip, upstreams  # noqa: F401

SERVERS = [("listen", "stdio"), ("listen", "http"), ("speak", "stdio"), ("speak", "http")]
STOP_TOOL = {"listen": "stop_listening", "speak": "stop"}
RECOVER_S = 3.0  # "within a few seconds"


def _fake(upstreams, name):
    flux, cartesia = upstreams
    return flux if name == "listen" else cartesia


async def _open(client: RelayClient, name: str):
    if name == "listen":
        ws = await client.open("listen", {"query": "model=flux-general-en&sample_rate=16000&encoding=linear16"})
        assert json.loads(await ws.recv())["type"] == "Connected"
    else:
        ws = await client.open("speak", {"query": "cartesia_version=2025-04-16", "version": "2025-04-16"})
    return ws


async def _exchange(ws, name: str, n: int = 0) -> None:
    """One turn heard, or one reply spoken, on this stream."""
    if name == "listen":
        await ws.send(b"\x00" * 32000)
        turns = [json.loads(await ws.recv()) for _ in range(2)]
        assert [t["event"] for t in turns] == ["StartOfTurn", "EndOfTurn"]
    else:
        ctx = f"c{n}"
        await ws.send(json.dumps({"transcript": "Hello there. ", "context_id": ctx, "continue": True,
                                  "output_format": {"sample_rate": 24000}}))  # fmt: skip
        assert [json.loads(await ws.recv())["type"] for _ in range(2)] == ["chunk", "timestamps"]
        await ws.send(json.dumps({"transcript": "", "context_id": ctx, "continue": False}))
        assert json.loads(await ws.recv())["type"] == "done"


async def _status(client: RelayClient) -> dict:
    return await client._conn.call("status")


def _named(events, name):
    return [d for n, d in events if n == name]


def _children(name: str) -> list[int]:
    out = subprocess.run(["pgrep", "-P", str(os.getpid()), "-f", f"servers.voice_{name}.server"],
                         capture_output=True, text=True).stdout  # fmt: skip
    return [int(p) for p in out.split()]


async def _ended(ws) -> None:
    """The stream is over: the socket reads end of file."""
    with pytest.raises((asyncio.IncompleteReadError, ConnectionError)):
        async with asyncio.timeout(5):
            while True:
                await ws.recv()


async def _closed_cleanly(client: RelayClient, events, ws, closes: int) -> None:
    """Close the stream; the server must report it and keep no stream or socket behind."""
    await ws.close()
    await _wait_until(lambda: len(_named(events, "voice_stream_closed")) >= closes)
    status = await _status(client)
    assert (status["streams"], status["sockets"]) == (0, 0)
    assert not client._calls


async def _wait_until(check, timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not check():
            await asyncio.sleep(0.02)


def _no_secrets(events) -> None:
    logged = json.dumps(events)
    assert DUMMY not in logged and "token" not in logged and ".sock" not in logged


# 1. The provider closes the stream ---------------------------------------------------------


@pytest.mark.parametrize("name,transport", SERVERS)
async def test_upstream_drop_then_reopen(upstreams, tmp_path, name, transport):
    fake = _fake(upstreams, name)
    events, log = _recorder()
    tasks_before = len(asyncio.all_tasks())
    client = RelayClient(name, transport, str(tmp_path / "data"), log)
    try:
        ws = await _open(client, name)
        await _exchange(ws, name)
        await fake.drop_all("network blip")
        await _ended(ws)
        # An interruption now finds the stream gone. Pipecat catches this and carries on.
        with pytest.raises(ConnectionError):
            await ws.send(json.dumps({"context_id": "c0", "cancel": True}))
        await _wait_for(events, "voice_stream_closed")
        assert _named(events, "voice_stream_closed")[0]["reason"] == "provider closed the stream (1011 network blip)"

        t0 = time.monotonic()
        ws2 = await _open(client, name)
        await _exchange(ws2, name, 1)
        assert time.monotonic() - t0 < RECOVER_S
        names = [n for n, _ in events]
        assert names.count("voice_stream_ready") == 2
        assert names.index("voice_stream_closed") < len(names) - 1 - names[::-1].index("voice_stream_ready")
        status = await _status(client)
        assert (status["streams"], status["sockets"]) == (1, 1)
        await _closed_cleanly(client, events, ws2, 2)
    finally:
        await client.close()
    assert fake.connections == 2
    await asyncio.sleep(0)
    assert len(asyncio.all_tasks()) <= tasks_before
    _no_secrets(events)


# 2. The provider refuses the reconnect once ------------------------------------------------


@pytest.mark.parametrize("name,transport", SERVERS)
async def test_refused_once_then_accepted(upstreams, tmp_path, name, transport):
    fake = _fake(upstreams, name)
    events, log = _recorder()
    client = RelayClient(name, transport, str(tmp_path / "data"), log)
    try:
        ws = await _open(client, name)
        await fake.drop_all()
        await _ended(ws)
        fake.refuse = 1
        with pytest.raises(ConnectionError, match="503"):
            await _open(client, name)
        refused = _named(events, "voice_stream_refused")
        assert len(refused) == 1 and "503" in refused[0]["reason"]
        assert (await _status(client))["streams"] == 0
        ws2 = await _open(client, name)
        await _exchange(ws2, name)
        await _closed_cleanly(client, events, ws2, 2)
    finally:
        await client.close()
    assert fake.refused == 1 and fake.connections == 2
    assert client.restarts == 0  # a refusal is the provider's, not a dead server
    _no_secrets(events)


# 3. The voice server process dies ----------------------------------------------------------


@pytest.mark.parametrize("name,transport", SERVERS)
async def test_server_dies_and_restarts(upstreams, tmp_path, name, transport):
    events, log = _recorder()
    client = RelayClient(name, transport, str(tmp_path / "data"), log)
    try:
        ws = await _open(client, name)
        await _exchange(ws, name)
        (pid,) = _children(name)
        os.kill(pid, signal.SIGKILL)
        await _ended(ws)
        await ws.close()  # what Pipecat does before it reconnects

        t0 = time.monotonic()
        ws2 = await _open(client, name)
        await _exchange(ws2, name, 1)
        assert time.monotonic() - t0 < RECOVER_S + 2  # includes starting a new server process
        assert client.restarts == 1
        restart = _named(events, "voice_server_restart")
        assert len(restart) == 1 and restart[0]["server"] == name
        assert len(_children(name)) == 1 and _children(name) != [pid]
        await _closed_cleanly(client, events, ws2, 1)
    finally:
        await client.close()
    assert _children(name) == []
    _no_secrets(events)


# 4. A long idle on the speak stream --------------------------------------------------------


@pytest.mark.parametrize("transport", ["stdio", "http"])
async def test_speak_idle_timeout_then_next_reply(upstreams, tmp_path, transport):
    _, cartesia = upstreams
    cartesia.idle_close_s = 0.5  # Cartesia's is five minutes
    events, log = _recorder()
    client = RelayClient("speak", transport, str(tmp_path / "data"), log)
    try:
        ws = await _open(client, "speak")
        await _exchange(ws, "speak")
        await asyncio.sleep(1.0)  # between replies, nothing to say
        await _ended(ws)
        await _wait_for(events, "voice_stream_closed")
        assert _named(events, "voice_stream_closed")[0]["reason"] == "provider closed the stream (1011 idle timeout)"
        ws2 = await _open(client, "speak")
        await _exchange(ws2, "speak", 1)
        await _closed_cleanly(client, events, ws2, 2)
    finally:
        await client.close()


# 5. A stop, or the bot stopping, during a reconnect ----------------------------------------


@pytest.mark.parametrize("name,transport", SERVERS)
async def test_stop_while_connecting(upstreams, tmp_path, name, transport):
    fake = _fake(upstreams, name)
    events, log = _recorder()
    client = RelayClient(name, transport, str(tmp_path / "data"), log)
    try:
        await _closed_cleanly(client, events, await _open(client, name), 1)
        fake.delay_s = 2.0  # a slow provider: the stream is still connecting
        opening = asyncio.create_task(_open(client, name))
        await asyncio.sleep(0.5)
        stopped = await client._conn.call(STOP_TOOL[name])
        assert len(stopped["stopped"]) == 1
        with pytest.raises(ConnectionError, match="stopped"):
            await opening
        assert _named(events, "voice_stream_closed")[-1]["reason"] == "stopped"
        assert len(_named(events, "voice_stream_ready")) == 1  # never offered
        assert (await _status(client))["streams"] == 0
        fake.delay_s = 0
        ws = await _open(client, name)
        await _exchange(ws, name)
        await _closed_cleanly(client, events, ws, 3)
    finally:
        await client.close()


@pytest.mark.parametrize("name,transport", SERVERS)
async def test_close_during_reconnect(upstreams, tmp_path, name, transport):
    fake = _fake(upstreams, name)
    events, log = _recorder()
    client = RelayClient(name, transport, str(tmp_path / "data"), log)
    ws = await _open(client, name)
    fake.delay_s = 5.0
    await fake.drop_all()
    await _ended(ws)
    opening = asyncio.create_task(_open(client, name))  # Pipecat's reconnect
    await asyncio.sleep(0.5)
    t0 = time.monotonic()
    await client.close()  # the bot stops meanwhile
    with pytest.raises(ConnectionError):
        await opening
    assert time.monotonic() - t0 < 3.0
    assert not client._calls and client._conn is None
    assert _children(name) == []
    with pytest.raises(ConnectionError, match="closed"):
        await client.open(name, {})


# The whole bot: both upstreams drop between two turns -----------------------------------


@pytest.mark.parametrize("transport", ["stdio", "http"])
async def test_bot_second_turn_after_drop(upstreams, tmp_path, clip, transport):  # noqa: F811
    flux, cartesia = upstreams
    flux.quiet_bytes = 16000
    flux.drop_after = 1  # Flux ends its stream right after the first turn
    cartesia.drop_after = 1  # Cartesia ends its stream after the first reply
    cfg = load_config("mac")
    cfg.providers["stages"] = {"stt": "voice_listen", "llm": "fake", "tts": "voice_speak"}
    cfg.providers["providers"]["cartesia"]["voice_id"] = cfg.providers["providers"]["cartesia"].get("voice_id") or "test-voice"
    cfg.profile["paths"] = {"logs_dir": str(tmp_path / "logs"), "data_dir": str(tmp_path / "data")}
    segments = [Segment(clip), Segment(clip, after="bot_speaking", delay_s=4.0)]
    r = await run_bot(cfg, input_mode="wav", segments=segments, mcp_transport=transport,
                      data_dir=str(tmp_path / "data"), handle_sigint=False, reply_timeout_s=30)  # fmt: skip
    events = [json.loads(line) for line in r.events_path.read_text().splitlines()]
    names = [e["event"] for e in events]
    assert "reply_timeout" not in names
    assert flux.connections >= 2 and cartesia.connections >= 2
    assert names.count("voice_transcript_final") == 2
    assert names.count("voice_audio_started") == 2
    spoken = {q.get("context_id") for q in cartesia.requests if "timer" in (q.get("transcript") or "").lower()}
    assert len(spoken) == 2  # two replies, each in its own context
    for server in ("listen", "speak"):
        closed = [e for e in events if e["event"] == "voice_stream_closed" and e.get("server") == server]
        assert any(e["reason"].startswith("provider closed the stream (1011") for e in closed)
        ready = [i for i, e in enumerate(events) if e["event"] == "voice_stream_ready" and e.get("server") == server]
        first_drop = next(i for i, e in enumerate(events) if e["event"] == "voice_stream_closed" and e.get("server") == server)
        assert any(i > first_drop for i in ready), f"{server}: no new stream after the drop"
    text = r.events_path.read_text()
    assert DUMMY not in text and ".sock" not in text
