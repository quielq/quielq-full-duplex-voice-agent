"""Stale Aura-2 audio after an interruption, replayed through a fake websocket (no spend)."""

import asyncio
import json

from harness.deepgram_tts import FreshSocketDeepgramTTSService
from pipecat.services.deepgram.tts import DeepgramTTSService

CLEARED = json.dumps({"type": "Cleared", "sequence_id": 0})
FLUSHED = json.dumps({"type": "Flushed", "sequence_id": 0})


class FakeSocket:
    """Yields scripted messages. A callable item is awaited instead, to change state mid-stream."""

    def __init__(self, script=()):
        self.script = script
        self.sent = []
        self.state = None
        self.closed = False

    async def send(self, message):
        self.sent.append(message)

    async def close(self):
        self.closed = True

    async def __aiter__(self):
        for item in self.script:
            if callable(item):
                await item()
            else:
                yield item


async def replay_upstream(script):
    """Stock service: (context, audio) for each chunk that would play, plus ended contexts."""
    service = DeepgramTTSService(api_key="test")
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

    steps = {"interrupt": interrupt, "next_reply": next_reply}
    service._websocket = FakeSocket([steps[i] if i in steps else i for i in script])
    await service._receive_messages()
    return [p for p in played if p[1] is not None], stops


def _service(events=None):
    service = FreshSocketDeepgramTTSService(
        api_key="test", on_event=lambda name, **f: (events if events is not None else []).append(name)
    )
    connects = []

    async def connect():
        connects.append(1)
        await asyncio.sleep(0)
        service._websocket = FakeSocket()

    service._connect = connect
    service.create_task = lambda coro, name=None: asyncio.create_task(coro)
    return service, connects


async def _speak(service):
    async for _ in service.run_tts("Hello.", "new"):
        pass


async def test_upstream_plays_old_audio_in_the_new_context():
    # Documents the bug; if this fails after a Pipecat bump, the workaround may be removable.
    script = [b"before", "interrupt", b"dropped", "next_reply", b"stale", FLUSHED, CLEARED]
    played, stops = await replay_upstream(script)
    assert ("new", b"stale") in played
    assert "new" in stops  # the stale Flushed ended the new reply's context


async def test_interruption_stops_reading_and_closes_the_old_socket():
    events = []
    service, connects = _service(events)
    old = service._websocket = FakeSocket()

    async def forever():
        await asyncio.sleep(60)

    receive = service._receive_task = asyncio.create_task(forever())

    async def cancel(task):
        task.cancel()

    service.cancel_task = cancel
    await service.on_audio_context_interrupted("old")
    assert service._websocket is None and service._receive_task is None
    assert json.loads(old.sent[-1]) == {"type": "Clear"}
    await service._reopening
    await asyncio.sleep(0)
    assert receive.cancelled() and old.closed
    assert connects == [1] and service._websocket is not old
    assert events == ["tts_socket_reopened"]


async def test_next_reply_waits_for_the_new_socket():
    service, _ = _service()
    old = service._websocket = FakeSocket()
    await service.on_audio_context_interrupted("old")
    await _speak(service)
    assert service._websocket is not old
    assert json.loads(service._websocket.sent[-1])["type"] == "Speak"
    assert not [m for m in old.sent if "Speak" in m]


async def test_two_contexts_reopen_once():
    service, connects = _service()
    service._websocket = FakeSocket()
    await service.on_audio_context_interrupted("a")
    await service.on_audio_context_interrupted("b")
    await service._reopening
    assert connects == [1]


async def test_interrupting_a_waiting_reply_keeps_the_reopen_running():
    service, connects = _service()
    service._websocket = FakeSocket()
    await service.on_audio_context_interrupted("old")
    waiting = asyncio.create_task(_speak(service))
    await asyncio.sleep(0)
    waiting.cancel()
    await asyncio.gather(waiting, return_exceptions=True)
    await asyncio.sleep(0.01)
    assert connects == [1] and service._websocket is not None
