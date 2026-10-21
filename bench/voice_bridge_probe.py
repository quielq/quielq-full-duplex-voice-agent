"""Step 6 probe: VoiceMCPBridge against the stub voice servers, over stdio and HTTP.

Measures the cost of the extra local MCP hop the review flags as a risk:
* event hop: server emits played_up_to / transcript_partial, bridge receives it;
* control round trip: duck, unduck, stop;
* stop to playback_stopped: stop call sent to the stopped event arriving.

    uv run python -m bench.voice_bridge_probe
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import statistics
import time

from harness.mcp_tools import http_server
from harness.voice_bridge import VoiceMCPBridge, VoiceServer

SPEAK = "servers.voice_speak.stub_server"
LISTEN = "servers.voice_listen.stub_server"
TEXT = " ".join(["word"] * 40)


def _pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(p * len(xs)))], 3) if xs else float("nan")


@contextlib.asynccontextmanager
async def _servers(transport: str):
    if transport == "stdio":
        yield VoiceServer("speak", module=SPEAK), VoiceServer("listen", module=LISTEN)
    else:
        async with http_server(SPEAK) as su, http_server(LISTEN) as lu:
            yield VoiceServer("speak", url=su), VoiceServer("listen", url=lu)


async def probe(transport: str) -> dict:
    log: list[dict] = []
    async with _servers(transport) as (speak, listen):
        bridge = VoiceMCPBridge(speak, listen, on_event=lambda e, **k: log.append({"e": e, "t": time.monotonic_ns(), **k}))
        await bridge.connect()
        try:
            # Barge-in rehearsal: playback runs, the user starts talking, the bridge ducks,
            # a command arrives, the bridge stops playback.
            playing = bridge.speak(TEXT, ms_per_word=50)
            await asyncio.sleep(0.6)
            heard = bridge.listen(script="stop please", lead_ms=0, ms_per_word=100)
            while not any(e["e"] == "voice_speech_started" for e in log):
                await asyncio.sleep(0.005)
            await bridge.duck(0.3)
            while not any(e["e"] == "voice_transcript_partial" for e in log):
                await asyncio.sleep(0.005)
            t_stop = time.monotonic_ns()
            await bridge.stop()
            result = await asyncio.wait_for(playing, 10)
            await asyncio.wait_for(heard, 10)
            await bridge.unduck()
            stopped = next(e for e in log if e["e"] == "voice_playback_stopped")
        finally:
            await bridge.close()

    hops = [e["hop_ms"] for e in log if e["e"] in ("voice_played_up_to", "voice_transcript_partial")]
    calls = {t: [e["rtt_ms"] for e in log if e["e"] == "voice_call" and e["tool"] == t] for t in ("duck", "stop", "unduck")}
    ducked = [e for e in log if e["e"] == "voice_played_up_to" and e.get("gain", 1) < 1]
    return {
        "transport": transport,
        "events": len(hops),
        "event_hop_ms_p50": _pct(hops, 0.5),
        "event_hop_ms_p95": _pct(hops, 0.95),
        "event_hop_ms_max": round(max(hops), 3),
        "duck_rtt_ms": calls["duck"],
        "stop_rtt_ms": calls["stop"],
        "stop_to_stopped_event_ms": round((stopped["t"] - t_stop) / 1e6, 1),
        "stopped_at_word": result.get("last_word_index"),
        "words_played_ducked": len(ducked),
    }


def main() -> None:
    for transport in ("stdio", "http"):
        print(json.dumps(asyncio.run(probe(transport))))


if __name__ == "__main__":
    main()
