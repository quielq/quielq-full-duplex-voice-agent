"""Music mock MCP server: a player state in $ME3_DATA_DIR/music.json, and no audio plays.

The plan is Spotify, for demos only, once a developer app exists. These tools are the
interface a real backend would keep.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from servers.common import ToolCallLog, data_dir, serve

mcp = FastMCP("music", instructions="A mock music player. Nothing really plays.")
mcp.add_middleware(ToolCallLog("music"))

DEFAULT = {"playing": False, "query": "", "volume": 50}


def _load() -> dict:
    p = data_dir() / "music.json"
    return json.loads(p.read_text()) if p.exists() else dict(DEFAULT)


def _save(state: dict) -> dict:
    (data_dir() / "music.json").write_text(json.dumps(state))
    return state


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
def get_now_playing() -> dict:
    """Read what is playing, and the volume."""
    return _load()


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
def play_music(query: Annotated[str, Field(min_length=1, description="Song, artist, or mood")]) -> dict:
    """Play music matching a search."""
    return _save(_load() | {"playing": True, "query": query})


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
def stop_music() -> dict:
    """Stop the music."""
    return _save(_load() | {"playing": False})


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
def set_volume(level: Annotated[int, Field(ge=0, le=100, description="Volume, 0 to 100")]) -> dict:
    """Set the music volume."""
    return _save(_load() | {"volume": level})


if __name__ == "__main__":
    serve(mcp)
