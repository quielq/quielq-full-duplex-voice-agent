"""How voice servers push events to the harness over MCP.

MCP has no custom notification types (``ServerNotification`` is a closed union), and on
the sessionless 2026-07-28 protocol a server notification that is not tied to an
in-flight request is dropped. So voice events are MCP logging notifications on the
``voice`` logger, sent while a long-running tool call (``speak``, ``listen``) is open.
The harness side is ``harness.voice_bridge.VoiceMCPBridge``. See ADR-008.

Each event carries ``t_emit_ns`` from ``time.monotonic_ns()``, which is one system-wide
clock on macOS and Linux, so the bridge can time the local MCP hop.
"""

from __future__ import annotations

import time
from typing import Any

from fastmcp import Context

VOICE_LOGGER = "voice"


async def emit(ctx: Context, event: str, **data: Any) -> None:
    await ctx.log(
        event,
        level="info",
        logger_name=VOICE_LOGGER,
        extra={"event": event, "t_emit_ns": time.monotonic_ns(), **data},
    )
