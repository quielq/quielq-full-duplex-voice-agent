"""Fake Flux and Cartesia WebSocket upstreams for the voice servers (ADR-032), on loopback.

Used by ``tests/test_voice_servers.py`` and ``bench/voice_hop_probe.py``. Nothing here reaches
a provider. Each fake speaks just enough of its provider's protocol for Pipecat's service and
our relay. In probe mode each fake also timestamps frames so the probe can time one way.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import time
from http import HTTPStatus

TRANSCRIPT = "set a timer for one minute"


class _Faults:
    """What a real provider does to a long stream: refuse a connect, drop, or time out idle.

    ``refuse`` turns away that many connection attempts with a 503, as a provider under load
    or a network blip would. ``drop_after`` closes each connection after that many turns or
    replies, with code 1011, the way Deepgram or Cartesia ends a stream on a server error.
    ``idle_close_s`` closes a connection that has had no message for that long, as Cartesia
    does after five minutes. ``delay_s`` holds each handshake that long, a slow provider.
    ``connections`` counts the handshakes that were let through.
    """

    def __init__(self) -> None:
        self.refuse = 0
        self.refused = 0
        self.drop_after = 0
        self.idle_close_s = 0.0
        self.delay_s = 0.0
        self.connections = 0
        self.live: set = set()

    async def process_request(self, connection, request):
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.refuse > 0:
            self.refuse -= 1
            self.refused += 1
            return connection.respond(HTTPStatus.SERVICE_UNAVAILABLE, "try again\n")
        return None

    def _opened(self, ws) -> None:
        self.connections += 1
        self.live.add(ws)

    async def _messages(self, ws):
        """The connection's messages, ending it after ``idle_close_s`` of quiet."""
        while True:
            try:
                if self.idle_close_s:
                    message = await asyncio.wait_for(ws.recv(), self.idle_close_s)
                else:
                    message = await ws.recv()
            except TimeoutError:
                await ws.close(1011, "idle timeout")
                return
            except Exception:
                return
            yield message

    async def drop_all(self, reason: str = "network blip") -> None:
        """Close every open connection now, from the provider's side."""
        for ws in list(self.live):
            with contextlib.suppress(Exception):
                await ws.close(1011, reason)


class FakeFlux(_Faults):
    """Sends Connected, then a whole turn each time enough audio has arrived and gone quiet.

    In probe mode it instead answers every audio frame at once with a ``ProbeEcho`` text
    message carrying the frame's sequence number (its first 4 bytes), and records when each
    frame arrived.
    """

    def __init__(self, audio_bytes: int = 32000):
        super().__init__()
        self.seen: list[dict] = []
        self.audio = 0
        self._needed = audio_bytes
        self.quiet_bytes = 0  # set to wait for this much silence after speech
        self.probe = False
        self.arrivals: dict[int, int] = {}  # sequence number to monotonic ns

        self.turns = 0  # turns sent, over every connection

    def _ended(self, state: dict, message: bytes) -> bool:
        if not self.quiet_bytes:
            state["audio"] += len(message)
            return state["audio"] >= self._needed
        state["quiet"] = 0 if any(message) else state["quiet"] + len(message)
        state["spoke"] = state["spoke"] or any(message)
        return state["spoke"] and state["quiet"] >= self.quiet_bytes

    async def handler(self, ws):
        req = ws.request
        self.seen.append({"path": req.path, "auth": req.headers.get("Authorization")})
        self._opened(ws)
        try:
            await self._serve(ws)
        finally:
            self.live.discard(ws)

    async def _serve(self, ws):
        await ws.send(json.dumps({"type": "Connected", "request_id": "fake", "sequence_id": 0}))
        state = {"audio": 0, "quiet": 0, "spoke": False}
        turns = 0
        async for message in self._messages(ws):
            if isinstance(message, bytes):
                if self.probe:
                    seq = int.from_bytes(message[:4], "big")
                    self.arrivals[seq] = time.monotonic_ns()
                    await ws.send(json.dumps({"type": "ProbeEcho", "seq": seq}))
                    continue
                self.audio += len(message)
                # Without a quiet gap to wait for, one connection gives one turn, as before.
                if (self.quiet_bytes or not turns) and self._ended(state, message):
                    state = {"audio": 0, "quiet": 0, "spoke": False}
                    for event, text in (("StartOfTurn", ""), ("EndOfTurn", TRANSCRIPT)):
                        await ws.send(json.dumps({
                            "type": "TurnInfo", "event": event, "turn_index": turns,
                            "audio_window_start": 0.0, "audio_window_end": 1.0,
                            "transcript": text, "words": [], "end_of_turn_confidence": 0.9,
                        }))  # fmt: skip
                    turns += 1
                    self.turns += 1
                    if self.drop_after and turns >= self.drop_after:
                        await ws.close(1011, "dropped by the fake")
                        return
            elif json.loads(message).get("type") == "CloseStream":
                break


class FakeCartesia(_Faults):
    """Answers each generation request with one chunk, its word timestamps, and done.

    A request with ``probe_chunks`` gets that many audio chunks of ``probe_chunk_ms`` each,
    one every ``probe_gap_ms``, each with a ``seq`` field, then done. The send time of each
    chunk is recorded under its context id and sequence number.
    """

    def __init__(self):
        super().__init__()
        self.seen: list[dict] = []
        self.requests: list[dict] = []
        self.sent: dict[tuple[str, int], int] = {}

    async def _probe(self, ws, msg: dict) -> None:
        ctx = msg.get("context_id", "")
        rate = msg["output_format"]["sample_rate"]
        pcm = b"\x00\x00" * (rate * msg.get("probe_chunk_ms", 20) // 1000)
        data = base64.b64encode(pcm).decode()
        gap = msg.get("probe_gap_ms", 20) / 1000
        for seq in range(msg["probe_chunks"]):
            self.sent[(ctx, seq)] = time.monotonic_ns()
            await ws.send(json.dumps({"type": "chunk", "context_id": ctx, "seq": seq, "data": data}))
            await asyncio.sleep(gap)
        await ws.send(json.dumps({"type": "done", "context_id": ctx, "done": True}))

    async def handler(self, ws):
        req = ws.request
        self.seen.append({"path": req.path, "key": req.headers.get("X-API-Key"),
                          "version": req.headers.get("Cartesia-Version")})  # fmt: skip
        self._opened(ws)
        try:
            await self._serve(ws)
        finally:
            self.live.discard(ws)

    async def _serve(self, ws):
        done: set[str] = set()
        async for message in self._messages(ws):
            msg = json.loads(message)
            self.requests.append(msg)
            if msg.get("probe_chunks"):
                await self._probe(ws, msg)
                continue
            ctx = msg.get("context_id", "")
            if msg.get("cancel") or not msg.get("transcript", "").strip() or ctx in done:
                if ctx and ctx not in done and not msg.get("cancel"):
                    done.add(ctx)
                    await ws.send(json.dumps({"type": "done", "context_id": ctx, "done": True}))
                    if self.drop_after and len(done) >= self.drop_after:
                        await ws.close(1011, "dropped by the fake")
                        return
                continue
            words = msg["transcript"].split()
            rate = msg["output_format"]["sample_rate"]
            pcm = b"\x00\x00" * (rate // 5 * len(words))
            await ws.send(json.dumps({"type": "chunk", "context_id": ctx, "data": base64.b64encode(pcm).decode()}))
            await ws.send(json.dumps({"type": "timestamps", "context_id": ctx, "word_timestamps": {
                "words": words, "start": [i * 0.2 for i in range(len(words))],
                "end": [(i + 1) * 0.2 for i in range(len(words))]}}))  # fmt: skip
