"""voice-listen (ADR-032): the MCP server that attaches to Deepgram Flux.

``listen`` opens one Flux stream and stays open while it lives. The harness sends the mic
audio and Flux's control messages on the stream's Unix socket and reads Flux's replies there.
This server holds the key, checks the daily cap, and reports turns as voice events.

    uv run python -m servers.voice_listen.server [--transport http --port 8767]
"""

from __future__ import annotations

from fastmcp import Context, FastMCP

from servers.common import ToolCallLog, serve
from servers.voice_relay import Relay, Upstream

FLUX_URL = "wss://api.deepgram.com/v2/listen"
CONTROL = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}


def flux_events(message: dict) -> tuple[str, dict] | None:
    """Flux's turn events, named as voice events. Interim updates stay on the socket only."""
    if message.get("type") != "TurnInfo":
        return None
    named = {"StartOfTurn": "speech_started", "EndOfTurn": "transcript_final",
             "EagerEndOfTurn": "transcript_eager", "TurnResumed": "speech_resumed"}  # fmt: skip
    if event := named.get(message.get("event", "")):
        return event, {"text": message.get("transcript", "")}
    return None


relay = Relay("voice-listen", Upstream(
    provider="deepgram",
    url=FLUX_URL,
    env_key="DEEPGRAM_API_KEY",
    auth=lambda key: {"Authorization": f"Token {key}"},
    test_env="ME3_VOICE_LISTEN_UPSTREAM",
    events=flux_events,
))  # fmt: skip

mcp = FastMCP("voice-listen", instructions="Speech input: one Deepgram Flux stream per listen call.")
mcp.add_middleware(ToolCallLog("voice-listen"))


@mcp.tool(annotations={**CONTROL, "idempotentHint": False})
async def listen(ctx: Context, query: str = "") -> dict:
    """Open a Flux stream with these query settings; stays open until the stream closes."""
    return await relay.open(ctx, query)


@mcp.tool(annotations=CONTROL)
def stop_listening(session: str = "") -> dict:
    """Close one stream, or all of them."""
    return relay.stop(session)


@mcp.tool(annotations={**CONTROL, "readOnlyHint": True, "openWorldHint": False})
def status() -> dict:
    """How many streams are open."""
    return relay.status()


if __name__ == "__main__":
    serve(mcp)
