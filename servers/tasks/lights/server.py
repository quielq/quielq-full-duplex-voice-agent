"""Lights mock MCP server: room state in $ME3_DATA_DIR/lights.json, no hardware.

Whether the Xiaomi bulb can be controlled locally is still open, so these tools are the
interface a real backend would keep.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from servers.common import ToolCallLog, data_dir, serve

mcp = FastMCP("lights", instructions="Mock room lights. Brightness is a percentage.")
mcp.add_middleware(ToolCallLog("lights"))

DEFAULT = {
    "bedroom": {"room": "bedroom", "is_on": False, "brightness": 100},
    "kitchen": {"room": "kitchen", "is_on": False, "brightness": 100},
    "living room": {"room": "living room", "is_on": True, "brightness": 60},
}


def _load() -> dict:
    p = data_dir() / "lights.json"
    return json.loads(p.read_text()) if p.exists() else json.loads(json.dumps(DEFAULT))


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
def list_lights() -> dict:
    """List each room's light, whether it is on, and its brightness."""
    return {"lights": list(_load().values())}


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
def set_light(
    room: Annotated[str, Field(min_length=1, description="Room name, from list_lights")],
    is_on: Annotated[bool, Field(description="True for on, false for off")],
    brightness: Annotated[int | None, Field(ge=1, le=100, description="Percent, if it should change")] = None,
) -> dict:
    """Turn a room's light on or off, and optionally set its brightness."""
    lights = _load()
    key = room.strip().lower()
    if key not in lights:
        return {"error": f"no light in {room!r}", "rooms": sorted(lights)}
    lights[key]["is_on"] = is_on
    if brightness is not None:
        lights[key]["brightness"] = brightness
    (data_dir() / "lights.json").write_text(json.dumps(lights))
    return lights[key]


if __name__ == "__main__":
    serve(mcp)
