"""Music MCP server. A mock by default; `--stages music=spotify` uses Spotify, for demos only.

A failed action returns {"error": ...}, so the harness never reports it as done (ADR-022).
"""

from __future__ import annotations

import functools
import os
from collections.abc import Callable
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from servers.common import ToolCallLog, serve
from servers.tasks.music.backends import MusicError, build_backend

mcp = FastMCP("music", instructions="A music player. It may be a mock, depending on the run.")
mcp.add_middleware(ToolCallLog("music"))


@functools.cache
def _backend():
    return build_backend(os.environ.get("ME3_MUSIC_BACKEND", "fake"))


def _run(action: Callable[[], dict]) -> dict:
    try:
        return action()
    except MusicError as e:
        return {"error": str(e)}


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}
)
def get_now_playing() -> dict:
    """Read what is playing, and the volume. The song changes by itself and from the screen, so call
    this every time you are asked what is playing; never answer from memory."""
    return _run(lambda: _backend().now_playing())


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}
)
def play_music(query: Annotated[str, Field(min_length=1, description="Song, artist, or mood")]) -> dict:
    """Search for music and start it. To continue paused music use resume_music, and to replay the current song use restart_track."""
    return _run(lambda: _backend().play(query))


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}
)
def stop_music() -> dict:
    """Pause the music. resume_music continues it from the same point."""
    return _run(lambda: _backend().stop())


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}
)
def resume_music() -> dict:
    """Continue paused music from where it stopped. Use this for resume, continue, or play it again after a pause."""
    return _run(lambda: _backend().resume())


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True}
)
def next_track() -> dict:
    """Skip to the next song."""
    return _run(lambda: _backend().next())


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True}
)
def previous_track() -> dict:
    """Go back to the previous song. Spotify may restart the current song instead."""
    return _run(lambda: _backend().previous())


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}
)
def restart_track() -> dict:
    """Play the current song again from its start. Use this for from the start, or start it over."""
    return _run(lambda: _backend().restart())


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}
)
def set_volume(level: Annotated[int, Field(ge=0, le=100, description="Volume, 0 to 100")]) -> dict:
    """Set the music volume."""
    return _run(lambda: _backend().set_volume(level))


if __name__ == "__main__":
    serve(mcp)
