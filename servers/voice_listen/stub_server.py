"""Stub voice-listen server (step 6): replays a scripted utterance, no ASR provider, no audio.

``listen`` stays open and streams ``speech_started``, ``transcript_partial``,
``transcript_final``, and ``speech_ended`` as voice events, then returns.
``stop_listening`` ends an open ``listen`` early.

    uv run python -m servers.voice_listen.stub_server [--transport http --port 8767]
"""

from __future__ import annotations

import argparse
import asyncio

from fastmcp import Context, FastMCP

from servers.common import ToolCallLog
from servers.voice_events import emit

mcp = FastMCP("voice-listen", instructions="Speech input control. Stub: scripted utterances.")
mcp.add_middleware(ToolCallLog("voice-listen"))

CONTROL = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
_stop = asyncio.Event()


@mcp.tool(annotations={**CONTROL, "idempotentHint": False})
async def listen(ctx: Context, script: str = "set a timer for one minute",
                 lead_ms: int = 200, ms_per_word: int = 250) -> dict:  # fmt: skip
    """Listen for one utterance; streams speech and transcript events until it ends."""
    _stop.clear()

    async def wait(ms: int) -> bool:
        try:
            await asyncio.wait_for(_stop.wait(), ms / 1000)
            return True
        except TimeoutError:
            return False

    if await wait(lead_ms):
        return {"stopped": True}
    await emit(ctx, "speech_started")
    words = script.split()
    for i in range(1, len(words) + 1):
        if await wait(ms_per_word):
            await emit(ctx, "speech_ended")
            return {"stopped": True}
        await emit(ctx, "transcript_partial", text=" ".join(words[:i]))
    await emit(ctx, "speech_ended")
    await emit(ctx, "transcript_final", text=script)
    return {"stopped": False, "text": script}


@mcp.tool(annotations=CONTROL)
def stop_listening() -> dict:
    """End an open listen call."""
    _stop.set()
    return {"ok": True}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    ap.add_argument("--port", type=int, default=8767)
    a = ap.parse_args()
    if a.transport == "stdio":
        mcp.run(transport="stdio", show_banner=False)
    else:
        mcp.run(transport="http", host="127.0.0.1", port=a.port, show_banner=False)


if __name__ == "__main__":
    main()
