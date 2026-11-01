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
    """Read what is playing, and the volume."""
    return _run(lambda: _backend().now_playing())


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}
)
def play_music(query: Annotated[str, Field(min_length=1, description="Song, artist, or mood")]) -> dict:
    """Play music matching a search."""
    return _run(lambda: _backend().play(query))


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}
)
def stop_music() -> dict:
    """Stop the music."""
    return _run(lambda: _backend().stop())


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}
)
def set_volume(level: Annotated[int, Field(ge=0, le=100, description="Volume, 0 to 100")]) -> dict:
    """Set the music volume."""
    return _run(lambda: _backend().set_volume(level))


if __name__ == "__main__":
    serve(mcp)
