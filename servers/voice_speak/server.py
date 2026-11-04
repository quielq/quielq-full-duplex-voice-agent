"""voice-speak (ADR-032): the MCP server that attaches to Cartesia.

``speak`` opens one Cartesia stream and stays open while it lives. The harness sends
generation requests on the stream's Unix socket and reads the audio chunks and word timestamps
there, in order. Playback, ducking, and echo cancelling stay in the harness, next to the
speaker. This server holds the key, checks the daily cap, and reports each reply as voice events.

    uv run python -m servers.voice_speak.server [--transport http --port 8766]
"""

from __future__ import annotations

from fastmcp import Context, FastMCP

from servers.common import ToolCallLog, serve
from servers.voice_relay import Relay, Upstream

CARTESIA_URL = "wss://api.cartesia.ai/tts/websocket"
CONTROL = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}


class _FirstChunk:
    """Name the first audio chunk of each reply, not every chunk."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def __call__(self, message: dict) -> tuple[str, dict] | None:
        ctx_id, kind = message.get("context_id", ""), message.get("type")
        if kind == "chunk" and ctx_id not in self._seen:
            self._seen.add(ctx_id)
            return "audio_started", {"context_id": ctx_id}
        if kind == "done":
            self._seen.discard(ctx_id)
            return "audio_done", {"context_id": ctx_id}
        if kind == "error":
            return "tts_error", {"context_id": ctx_id, "error": str(message.get("error", message.get("title", "")))[:200]}
        return None


relay = Relay("voice-speak", Upstream(
    provider="cartesia",
    url=CARTESIA_URL,
    env_key="CARTESIA_API_KEY",
    auth=lambda key: {"X-API-Key": key},
    test_env="ME3_VOICE_SPEAK_UPSTREAM",
    events=_FirstChunk(),
    headers=frozenset({"Cartesia-Version"}),
))  # fmt: skip

mcp = FastMCP("voice-speak", instructions="Speech output: one Cartesia stream per speak call.")
mcp.add_middleware(ToolCallLog("voice-speak"))


@mcp.tool(annotations={**CONTROL, "idempotentHint": False})
async def speak(ctx: Context, query: str = "", version: str = "") -> dict:
    """Open a Cartesia stream; stays open until the stream closes."""
    return await relay.open(ctx, query, {"Cartesia-Version": version} if version else None)


@mcp.tool(annotations=CONTROL)
def stop(session: str = "") -> dict:
    """Close one stream, or all of them. Cancelling one reply is a message on the stream."""
    return relay.stop(session)


@mcp.tool(annotations={**CONTROL, "readOnlyHint": True, "openWorldHint": False})
def status() -> dict:
    """How many streams are open."""
    return relay.status()


if __name__ == "__main__":
    serve(mcp)
