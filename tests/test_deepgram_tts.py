"""Stale Aura-2 audio after an interruption, replayed through a fake websocket (no spend)."""

import asyncio
import json
import time

import pytest

from harness import deepgram_tts
from harness.deepgram_tts import ClearAwareDeepgramTTSService
from pipecat.services.deepgram.tts import DeepgramTTSService

CLEARED = json.dumps({"type": "Cleared", "sequence_id": 0})
FLUSHED = json.dumps({"type": "Flushed", "sequence_id": 0})


class FakeSocket:
    """Yields scripted messages. A callable item is awaited instead, to change state mid-stream."""

    def __init__(self, script):
        self.script = script
        self.sent = []
        self.state = None

    async def send(self, message):
        self.sent.append(message)

    async def __aiter__(self):
        for item in self.script:
            if callable(item):
                await item()
            else:
                yield item


async def replay(service, script):
    """Run ``script`` and return (context, audio) for each chunk that would play, plus stops."""
    played, stops = [], []
    context = {"id": "old"}

    async def append(ctx, frame):
        played.append((ctx, frame.audio if hasattr(frame, "audio") else None))

    async def remove(ctx):
        stops.append(ctx)

    service.append_to_audio_context = append
    service.remove_audio_context = remove
    service.get_active_audio_context_id = lambda: context["id"]

    async def interrupt():
        await service.on_audio_context_interrupted("old")
        context["id"] = None

    async def next_reply():
        context["id"] = "new"

    resolved = [
        {"interrupt": interrupt, "next_reply": next_reply}.get(item, item)
        if isinstance(item, str)
        else item
        for item in script
    ]
    service._websocket = FakeSocket(resolved)
    await service._receive_messages()
    return [p for p in played if p[1] is not None], stops, service._websocket.sent


SCRIPT = [b"before", "interrupt", b"dropped", "next_reply", b"stale", FLUSHED, CLEARED, b"fresh"]


async def test_upstream_plays_old_audio_in_the_new_context():
    # Documents the bug; if this fails after a Pipecat bump, the workaround may be removable.
    played, stops, _ = await replay(DeepgramTTSService(api_key="test"), SCRIPT)
    assert ("new", b"stale") in played
    assert "new" in stops  # the stale Flushed ended the new reply's context


async def test_audio_before_cleared_is_discarded():
    events = []
    service = ClearAwareDeepgramTTSService(
        api_key="test", on_event=lambda name, **f: events.append((name, f))
    )
    played, stops, sent = await replay(service, SCRIPT)
    assert played == [("old", b"before"), ("new", b"fresh")]
    assert stops == []
    assert json.loads(sent[0]) == {"type": "Clear"}
    assert [name for name, _ in events] == ["tts_clear_sent", "tts_cleared"]
    assert events[1][1]["stale_chunks"] == 2 and events[1][1]["timed_out"] is False


async def test_two_interruptions_wait_for_both_cleared_replies():
    service = ClearAwareDeepgramTTSService(api_key="test")
    script = ["interrupt", "interrupt", "next_reply", CLEARED, b"stale", CLEARED, b"fresh"]
    played, _, sent = await replay(service, script)
    assert len(sent) == 2
    assert played == [("new", b"fresh")]


def _fake_reconnect(service):
    calls = []

    async def drop():
        calls.append("drop")
        service._websocket = None

    async def connect():
        calls.append("connect")
        service._websocket = FakeSocket([])

    service._drop_socket = drop
    service._connect = connect
    return calls


async def _speak(service):
    async for _ in service.run_tts("Hello.", "new"):
        pass


async def test_next_reply_waits_for_cleared_on_the_same_socket():
    service = ClearAwareDeepgramTTSService(api_key="test")
    calls = _fake_reconnect(service)
    socket = service._websocket = FakeSocket([])
    await service.on_audio_context_interrupted("old")
    speak = asyncio.create_task(_speak(service))
    await asyncio.sleep(0)
    assert not [m for m in socket.sent if "Speak" in m]  # held until Cleared
    assert service._keep_message(CLEARED)
    await speak
    assert calls == []
    assert json.loads(socket.sent[-1])["type"] == "Speak"


async def test_ignored_clear_replaces_the_socket_before_the_next_reply(monkeypatch):
    # Seen live: no Cleared, and the old reply kept streaming until the socket closed.
    monkeypatch.setattr(deepgram_tts, "CLEAR_TIMEOUT_S", 0.01)
    events = []
    service = ClearAwareDeepgramTTSService(
        api_key="test", on_event=lambda name, **f: events.append((name, f))
    )
    calls = _fake_reconnect(service)
    old = service._websocket = FakeSocket([])
    await service.on_audio_context_interrupted("old")
    assert not service._keep_message(b"stale")
    await _speak(service)
    assert calls == ["drop", "connect"]
    assert not [m for m in old.sent if "Speak" in m]
    assert json.loads(service._websocket.sent[-1])["type"] == "Speak"
    assert events[-1][1]["timed_out"] is True and events[-1][1]["stale_chunks"] == 1
    assert service._keep_message(b"fresh")


@pytest.mark.parametrize("message", ["not json", json.dumps({"type": "Warning"})])
async def test_other_text_messages_pass_through_while_clearing(message):
    service = ClearAwareDeepgramTTSService(api_key="test")
    service._clears_pending = 1
    service._clear_sent_at = time.monotonic()
    assert service._keep_message(message)
