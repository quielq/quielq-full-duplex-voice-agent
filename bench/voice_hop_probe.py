"""How much latency the ADR-032 voice servers add to audio frames, on loopback fakes only.

A fake Flux and a fake Cartesia run in this process on 127.0.0.1, and each voice server is
pointed at them with its test variable. The keys are dummy values, so nothing reaches a
provider and nothing is spent. For each MCP transport (stdio, HTTP) the probe times the same
traffic two ways: through ``harness.voice_relay.RelayClient`` and the voice server, and with a
direct ``websockets.connect`` to the same fake. The difference is the hop.

* listen_up_ms: a 20 ms, 16 kHz audio frame, from send to its arrival at the fake Flux.
* listen_rtt_ms: the same frame, from send to the fake's echo arriving back.
* speak_down_ms: a 24 kHz Cartesia audio chunk, from the fake's send to its arrival here.
* speak_rtt_ms: a generation request, from send to the first audio chunk arriving.
* open_ms: relay, from ``listen`` or ``speak`` to the ``upstream_connected`` event; direct,
  the WebSocket handshake. The first relay open also starts the server and is kept apart.

Every clock read is ``time.monotonic_ns`` in this process, so one-way times need no clock sync.
Frames are paced at 20 ms, as a microphone would send them. The fakes share this event loop,
so both paths carry the same scheduling noise.

    uv run python -m bench.voice_hop_probe [--frames 250] [--opens 10] [--transports stdio,http]
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

import websockets

from bench.voice_fakes import FakeCartesia, FakeFlux
from harness.voice_relay import RelayClient

REPO_ROOT = Path(__file__).resolve().parents[1]
DUMMY = "dummy-probe-value"  # never a real key
LISTEN_QUERY = "model=flux-general-en&sample_rate=16000&encoding=linear16"
SPEAK_QUERY = "cartesia_version=2025-04-16"
SPEAK_VERSION = "2025-04-16"
FRAME_MS = 20
UP_RATE, DOWN_RATE = 16000, 24000
UP_BYTES = UP_RATE * 2 * FRAME_MS // 1000  # 640 bytes of linear16
METRICS = ("listen_up_ms", "listen_rtt_ms", "speak_down_ms", "speak_rtt_ms", "open_ms")


def stats(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0, "p50": None, "p95": None, "max": None}
    s = sorted(xs)
    return {
        "n": len(s),
        "p50": round(statistics.median(s), 3),
        "p95": round(s[min(len(s) - 1, int(0.95 * len(s)))], 3),
        "max": round(s[-1], 3),
    }


def _ms(ns: int) -> float:
    return ns / 1e6


@contextlib.contextmanager
def _loopback_env(flux_url: str, cartesia_url: str, data_dir: str):
    """Point both voice servers at the fakes with dummy keys; put everything back after."""
    values = {
        "ME3_VOICE_LISTEN_UPSTREAM": flux_url,
        "ME3_VOICE_SPEAK_UPSTREAM": cartesia_url,
        "DEEPGRAM_API_KEY": DUMMY,
        "CARTESIA_API_KEY": DUMMY,
        "ME3_DATA_DIR": data_dir,
        "FASTMCP_LOG_LEVEL": "WARNING",  # the servers' per-event debug lines
        "PYTHONWARNINGS": "ignore",  # and the logging deprecation warning on every event
    }
    saved = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class _Events:
    """Voice events from the relay, with the time each one arrived here."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, int]] = []
        self._changed = asyncio.Event()

    def __call__(self, name: str, **data) -> None:
        self.seen.append((name, time.monotonic_ns()))
        self._changed.set()

    async def wait(self, name: str, after: int, timeout: float = 10.0) -> int:
        async with asyncio.timeout(timeout):
            while True:
                for n, t in self.seen:
                    if n == name and t >= after:
                        return t
                self._changed.clear()
                await self._changed.wait()


async def _listen_traffic(ws, flux: FakeFlux, frames: int) -> tuple[list[float], list[float]]:
    """Paced 20 ms frames up, echoes back. Returns one-way and round-trip times in ms."""
    flux.arrivals.clear()
    msg = json.loads(await ws.recv())
    assert msg["type"] == "Connected", msg
    sent: dict[int, int] = {}
    back: dict[int, int] = {}

    async def reader():
        while len(back) < frames:
            msg = json.loads(await ws.recv())
            if msg.get("type") == "ProbeEcho":
                back[msg["seq"]] = time.monotonic_ns()

    task = asyncio.create_task(reader())
    pad = b"\x00" * (UP_BYTES - 4)
    start = time.monotonic()
    for seq in range(frames):
        sent[seq] = time.monotonic_ns()
        await ws.send(seq.to_bytes(4, "big") + pad)
        await asyncio.sleep(max(0.0, start + (seq + 1) * FRAME_MS / 1000 - time.monotonic()))
    await asyncio.wait_for(task, 10)
    up = [_ms(flux.arrivals[s] - sent[s]) for s in sent]
    rtt = [_ms(back[s] - sent[s]) for s in sent]
    return up, rtt


async def _speak_traffic(ws, cartesia: FakeCartesia, chunks: int, ctx: str) -> tuple[list[float], float]:
    """One request for paced 24 kHz chunks. Returns one-way chunk times and the request RTT."""
    t0 = time.monotonic_ns()
    await ws.send(json.dumps({
        "transcript": "probe", "context_id": ctx, "continue": False,
        "output_format": {"container": "raw", "encoding": "pcm_s16le", "sample_rate": DOWN_RATE},
        "probe_chunks": chunks, "probe_chunk_ms": FRAME_MS, "probe_gap_ms": FRAME_MS,
    }))  # fmt: skip
    got: dict[int, int] = {}
    first = None
    while True:
        msg = json.loads(await ws.recv())
        now = time.monotonic_ns()
        if msg.get("type") == "chunk" and msg.get("context_id") == ctx:
            first = first or now
            got[msg["seq"]] = now
        elif msg.get("type") == "done":
            break
    down = [_ms(t - cartesia.sent[(ctx, seq)]) for seq, t in got.items()]
    return down, _ms(first - t0)


async def _direct(url: str, headers: dict) -> tuple[websockets.ClientConnection, float]:
    t0 = time.monotonic_ns()
    ws = await websockets.connect(url, additional_headers=headers)
    return ws, _ms(time.monotonic_ns() - t0)


async def _probe_transport(transport: str, flux_url: str, cart_url: str, flux: FakeFlux,
                           cartesia: FakeCartesia, frames: int, opens: int, data_dir: str) -> dict:  # fmt: skip
    out: dict[str, dict] = {m: {"direct": [], "relay": []} for m in METRICS}
    listen_url, speak_url = f"{flux_url}?{LISTEN_QUERY}", f"{cart_url}?{SPEAK_QUERY}"
    listen_headers = {"Authorization": f"Token {DUMMY}"}
    speak_headers = {"X-API-Key": DUMMY, "Cartesia-Version": SPEAK_VERSION}
    rtt_requests = max(1, min(frames, 20))

    # Direct to the fakes.
    ws, _ = await _direct(listen_url, listen_headers)
    up, rtt = await _listen_traffic(ws, flux, frames)
    out["listen_up_ms"]["direct"], out["listen_rtt_ms"]["direct"] = up, rtt
    await ws.close()
    ws, _ = await _direct(speak_url, speak_headers)
    down, _ = await _speak_traffic(ws, cartesia, frames, f"d-{transport}")
    out["speak_down_ms"]["direct"] = down
    for i in range(rtt_requests):
        _, r = await _speak_traffic(ws, cartesia, 1, f"dr-{transport}-{i}")
        out["speak_rtt_ms"]["direct"].append(r)
    await ws.close()
    for _ in range(opens):
        for url, headers in ((listen_url, listen_headers), (speak_url, speak_headers)):
            ws, t = await _direct(url, headers)
            out["open_ms"]["direct"].append(t)
            await ws.close()

    # Through the voice servers.
    events = _Events()
    cold: dict[str, float] = {}
    listen, speak = RelayClient("listen", transport, data_dir, events), RelayClient("speak", transport, data_dir, events)
    try:

        async def relay_open(client: RelayClient, tool: str, args: dict):
            t0 = time.monotonic_ns()
            ws = await client.open(tool, args)
            t = await events.wait("voice_upstream_connected", t0)
            return ws, _ms(t - t0)

        listen_args = {"query": LISTEN_QUERY}
        speak_args = {"query": SPEAK_QUERY, "version": SPEAK_VERSION}
        ws, cold["listen"] = await relay_open(listen, "listen", listen_args)
        up, rtt = await _listen_traffic(ws, flux, frames)
        out["listen_up_ms"]["relay"], out["listen_rtt_ms"]["relay"] = up, rtt
        await ws.close()
        ws, cold["speak"] = await relay_open(speak, "speak", speak_args)
        down, _ = await _speak_traffic(ws, cartesia, frames, f"r-{transport}")
        out["speak_down_ms"]["relay"] = down
        for i in range(rtt_requests):
            _, r = await _speak_traffic(ws, cartesia, 1, f"rr-{transport}-{i}")
            out["speak_rtt_ms"]["relay"].append(r)
        await ws.close()
        for _ in range(opens):
            for client, tool, args in ((listen, "listen", listen_args), (speak, "speak", speak_args)):
                ws, t = await relay_open(client, tool, args)
                out["open_ms"]["relay"].append(t)
                await ws.close()
    finally:
        await listen.close()
        await speak.close()

    result: dict = {}
    for metric, paths in out.items():
        d, r = stats(paths["direct"]), stats(paths["relay"])
        added = round(r["p50"] - d["p50"], 3) if d["n"] and r["n"] else None
        result[metric] = {"direct": d, "relay": r, "added_p50_ms": added}
    result["cold_open_ms"] = {k: round(v, 3) for k, v in cold.items()}
    return result


async def probe(frames: int = 250, opens: int = 10, transports: tuple[str, ...] = ("stdio", "http")) -> dict:
    flux, cartesia = FakeFlux(), FakeCartesia()
    flux.probe = True
    with tempfile.TemporaryDirectory(prefix="me3-hop-") as tmp:
        async with (websockets.serve(flux.handler, "127.0.0.1", 0) as f,
                    websockets.serve(cartesia.handler, "127.0.0.1", 0) as c):  # fmt: skip
            fport, cport = f.sockets[0].getsockname()[1], c.sockets[0].getsockname()[1]
            flux_url, cart_url = f"ws://127.0.0.1:{fport}/v2/listen", f"ws://127.0.0.1:{cport}/tts/websocket"
            data_dir = str(Path(tmp) / "data")
            with _loopback_env(flux_url, cart_url, data_dir):
                results = {}
                for transport in transports:
                    results[transport] = await _probe_transport(
                        transport, flux_url, cart_url, flux, cartesia, frames, opens, data_dir)  # fmt: skip
    return {
        "probe": "voice_hop",
        "when": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "machine": {"system": platform.system(), "release": platform.release(),
                    "arch": platform.machine(), "python": platform.python_version()},  # fmt: skip
        "frames": frames,
        "opens": opens,
        "frame_ms": FRAME_MS,
        "up": {"rate_hz": UP_RATE, "bytes": UP_BYTES},
        "down": {"rate_hz": DOWN_RATE, "chunk_ms": FRAME_MS, "bytes": DOWN_RATE * 2 * FRAME_MS // 1000},
        "results": results,
    }


def table(report: dict) -> str:
    def f(x):
        return "   -" if x is None else f"{x:7.3f}"

    head = f"{'transport':9} {'metric':14} {'direct p50':>10} {'p95':>7} {'max':>7}  {'relay p50':>9} {'p95':>7} {'max':>7}  {'added p50':>9}"
    lines = [head, "-" * len(head)]
    for transport, res in report["results"].items():
        for metric in METRICS:
            row = res[metric]
            d, r = row["direct"], row["relay"]
            lines.append(f"{transport:9} {metric:14} {f(d['p50']):>10} {f(d['p95'])} {f(d['max'])}  "
                         f"{f(r['p50']):>9} {f(r['p95'])} {f(r['max'])}  {f(row['added_p50_ms']):>9}")  # fmt: skip
        cold = res["cold_open_ms"]
        lines.append(f"{transport:9} {'cold open':14} listen {cold.get('listen', 0):.1f} ms, speak {cold.get('speak', 0):.1f} ms (server start included)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--frames", type=int, default=250, help="audio frames per path (20 ms each)")
    ap.add_argument("--opens", type=int, default=10, help="stream opens per path after the first")
    ap.add_argument("--transports", default="stdio,http")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "logs", help="folder for the JSON report")
    args = ap.parse_args(argv)
    report = asyncio.run(probe(args.frames, args.opens, tuple(args.transports.split(","))))
    print(f"voice hop probe: {args.frames} frames, {args.opens} opens, {report['machine']['system']} {report['machine']['arch']}")
    print(table(report))
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"voice-hop-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
