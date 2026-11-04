"""Fake Flux and Cartesia WebSocket upstreams for the voice servers (ADR-032), on loopback.

Used by ``tests/test_voice_servers.py`` and ``bench/voice_hop_probe.py``. Nothing here reaches
a provider. Each fake speaks just enough of its provider's protocol for Pipecat's service and
our relay. In probe mode each fake also timestamps frames so the probe can time one way.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time

TRANSCRIPT = "set a timer for one minute"


class FakeFlux:
    """Sends Connected, then a whole turn once enough audio has arrived and gone quiet.

    In probe mode it instead answers every audio frame at once with a ``ProbeEcho`` text
    message carrying the frame's sequence number (its first 4 bytes), and records when each
    frame arrived.
    """

    def __init__(self, audio_bytes: int = 32000):
        self.seen: list[dict] = []
        self.audio = 0
        self._needed = audio_bytes
        self.quiet_bytes = 0  # set to wait for this much silence after speech
        self.probe = False
        self.arrivals: dict[int, int] = {}  # sequence number to monotonic ns

    def _ended(self, message: bytes) -> bool:
        if not self.quiet_bytes:
            return self.audio >= self._needed
        self._quiet = 0 if any(message) else getattr(self, "_quiet", 0) + len(message)
        self._spoke = getattr(self, "_spoke", False) or any(message)
        return self._spoke and self._quiet >= self.quiet_bytes

    async def handler(self, ws):
        req = ws.request
        self.seen.append({"path": req.path, "auth": req.headers.get("Authorization")})
        await ws.send(json.dumps({"type": "Connected", "request_id": "fake", "sequence_id": 0}))
        sent = False
        async for message in ws:
            if isinstance(message, bytes):
                if self.probe:
                    seq = int.from_bytes(message[:4], "big")
                    self.arrivals[seq] = time.monotonic_ns()
                    await ws.send(json.dumps({"type": "ProbeEcho", "seq": seq}))
                    continue
                self.audio += len(message)
                if not sent and self._ended(message):
                    sent = True
                    for event, text in (("StartOfTurn", ""), ("EndOfTurn", TRANSCRIPT)):
                        await ws.send(json.dumps({
                            "type": "TurnInfo", "event": event, "turn_index": 0,
                            "audio_window_start": 0.0, "audio_window_end": 1.0,
                            "transcript": text, "words": [], "end_of_turn_confidence": 0.9,
                        }))  # fmt: skip
            elif json.loads(message).get("type") == "CloseStream":
                break


class FakeCartesia:
    """Answers each generation request with one chunk, its word timestamps, and done.

    A request with ``probe_chunks`` gets that many audio chunks of ``probe_chunk_ms`` each,
    one every ``probe_gap_ms``, each with a ``seq`` field, then done. The send time of each
    chunk is recorded under its context id and sequence number.
    """

    def __init__(self):
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
        done: set[str] = set()
        async for message in ws:
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
                continue
            words = msg["transcript"].split()
            rate = msg["output_format"]["sample_rate"]
            pcm = b"\x00\x00" * (rate // 5 * len(words))
            await ws.send(json.dumps({"type": "chunk", "context_id": ctx, "data": base64.b64encode(pcm).decode()}))
            await ws.send(json.dumps({"type": "timestamps", "context_id": ctx, "word_timestamps": {
                "words": words, "start": [i * 0.2 for i in range(len(words))],
                "end": [(i + 1) * 0.2 for i in range(len(words))]}}))  # fmt: skip
