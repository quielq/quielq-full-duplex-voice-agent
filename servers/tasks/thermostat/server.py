"""Thermostat mock MCP server: a setpoint in $ME3_DATA_DIR/thermostat.json, no hardware."""

from __future__ import annotations

import json
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from servers.common import ToolCallLog, data_dir, serve

mcp = FastMCP("thermostat", instructions="A mock room thermostat. Temperatures are in Celsius.")
mcp.add_middleware(ToolCallLog("thermostat"))

DEFAULT = {"target_c": 24.0, "mode": "cool"}


def _load() -> dict:
    p = data_dir() / "thermostat.json"
    return json.loads(p.read_text()) if p.exists() else dict(DEFAULT)


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
def get_thermostat() -> dict:
    """Read the current setpoint and mode."""
    return _load()


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
def set_thermostat(
    target_c: Annotated[float, Field(ge=16, le=30, description="Target temperature in Celsius")],
) -> dict:
    """Set the target temperature."""
    state = _load() | {"target_c": target_c}
    (data_dir() / "thermostat.json").write_text(json.dumps(state))
    return state


if __name__ == "__main__":
    serve(mcp)
