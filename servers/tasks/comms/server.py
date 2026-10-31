"""Comms mock MCP server: messages go to $ME3_DATA_DIR/outbox.json and nowhere else.

``send_message`` is marked destructive and open world because the real one would be, so the
tool policy asks for spoken confirmation and then holds it for the undo window.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from servers.common import ToolCallLog, data_dir, serve

mcp = FastMCP("comms", instructions="A mock messenger. Nothing is really sent.")
mcp.add_middleware(ToolCallLog("comms"))


def _load() -> dict:
    p = data_dir() / "outbox.json"
    return json.loads(p.read_text()) if p.exists() else {}


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True}
)
def send_message(
    to: Annotated[str, Field(min_length=1, description="Contact name")],
    body: Annotated[str, Field(min_length=1, description="The message text")],
) -> dict:
    """Send a message to a contact."""
    message = {"id": uuid.uuid4().hex[:8], "to": to, "body": body, "sent_at": time.time()}
    outbox = _load()
    outbox[message["id"]] = message
    (data_dir() / "outbox.json").write_text(json.dumps(outbox))
    return {"sent": True, "id": message["id"], "to": to}


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
def list_sent_messages() -> dict:
    """List messages already sent, oldest first."""
    return {"messages": sorted(_load().values(), key=lambda m: m["sent_at"])}


if __name__ == "__main__":
    serve(mcp)
