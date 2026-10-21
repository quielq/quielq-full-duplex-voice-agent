"""Stub voice-speak server (step 6): simulates playback, no TTS provider, no audio.

``speak`` stays open while the utterance "plays" and streams ``playback_started``,
``played_up_to`` (utterance id, word index, ms offset) and ``playback_finished`` as voice
events. ``stop``, ``duck``, and ``unduck`` act on the playing utterance from a second,
concurrent call. Real audio will move on a local side channel, not over MCP.

    uv run python -m servers.voice_speak.stub_server [--transport http --port 8766]
"""

from __future__ import annotations

import argparse
import asyncio
import uuid

from fastmcp import Context, FastMCP

from servers.common import ToolCallLog
from servers.voice_events import emit

mcp = FastMCP("voice-speak", instructions="Speech output control. Stub: simulated playback.")
mcp.add_middleware(ToolCallLog("voice-speak"))

CONTROL = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}


class _Playback:
    def __init__(self) -> None:
        self.utterance_id: str | None = None
        self.stop = asyncio.Event()
        self.gain = 1.0


_state = _Playback()


@mcp.tool(annotations={**CONTROL, "idempotentHint": False})
async def speak(text: str, ctx: Context, ms_per_word: int = 300, utterance_id: str = "") -> dict:
    """Play text; streams played_up_to events until it finishes or is stopped."""
    uid = utterance_id or uuid.uuid4().hex[:8]
    _state.utterance_id, _state.stop = uid, asyncio.Event()
    words = text.split()
    await emit(ctx, "playback_started", utterance_id=uid, words=len(words))
    for i, word in enumerate(words):
        try:
            await asyncio.wait_for(_state.stop.wait(), ms_per_word / 1000)
            await emit(ctx, "playback_stopped", utterance_id=uid, word_index=i - 1,
                       ms_offset=i * ms_per_word)  # fmt: skip
            return {"utterance_id": uid, "stopped": True, "last_word_index": i - 1}
        except TimeoutError:
            pass
        await emit(ctx, "played_up_to", utterance_id=uid, word_index=i, word=word,
                   ms_offset=(i + 1) * ms_per_word, gain=_state.gain)  # fmt: skip
    await emit(ctx, "playback_finished", utterance_id=uid)
    _state.utterance_id = None
    return {"utterance_id": uid, "stopped": False, "last_word_index": len(words) - 1}


@mcp.tool(annotations=CONTROL)
def stop() -> dict:
    """Stop the current utterance at the next word boundary."""
    _state.stop.set()
    return {"utterance_id": _state.utterance_id}


@mcp.tool(annotations=CONTROL)
def duck(gain: float = 0.3) -> dict:
    """Lower playback volume (0 to 1)."""
    _state.gain = max(0.0, min(1.0, gain))
    return {"gain": _state.gain}


@mcp.tool(annotations=CONTROL)
def unduck() -> dict:
    """Restore playback volume."""
    _state.gain = 1.0
    return {"gain": 1.0}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    ap.add_argument("--port", type=int, default=8766)
    a = ap.parse_args()
    if a.transport == "stdio":
        mcp.run(transport="stdio", show_banner=False)
    else:
        mcp.run(transport="http", host="127.0.0.1", port=a.port, show_banner=False)


if __name__ == "__main__":
    main()
