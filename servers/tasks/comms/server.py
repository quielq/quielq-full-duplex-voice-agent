"""Comms MCP server: messages and calls through the Mac phone bridge (ADR-024).

The stage ``comms`` in configs/providers.yaml picks the backend. ``mock`` writes to
$ME3_DATA_DIR/outbox.json and nowhere else. ``bridge`` posts to scripts/mac_phone_bridge.py,
from this Mac or from the Pi, and reaches only names in $ME3_DATA_DIR/contacts.yaml: the
model never sees or supplies a number, so injected text cannot name a new recipient.

``send_message`` and ``call_contact`` are destructive and open world, so the tool policy reads
them aloud, waits for a yes, and holds them for the undo window. A bridge call never retries:
a timeout may still have sent, and a second try could send twice.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Annotated

import httpx
import yaml
from fastmcp import FastMCP
from pydantic import Field

from bench.cost_ledger import SpendRefused
from servers.common import ToolCallLog, data_dir, ledger, price, serve, tool_stage

mcp = FastMCP("comms", instructions="Send messages and start phone calls to saved contacts.")
mcp.add_middleware(ToolCallLog("comms"))

BRIDGE_TIMEOUT_S = 22.0   # the bridge gives osascript 20 s; under the policy timeout


def _load() -> dict:
    p = data_dir() / "outbox.json"
    return json.loads(p.read_text()) if p.exists() else {}


def _keep(record: dict) -> dict:
    outbox = _load()
    outbox[record["id"]] = record
    (data_dir() / "outbox.json").write_text(json.dumps(outbox))
    return record


def _contacts() -> dict[str, tuple[str, str]]:
    """Lower-case name to (name as saved, number)."""
    p = data_dir() / "contacts.yaml"
    saved = (yaml.safe_load(p.read_text()) or {}).get("contacts") or {} if p.exists() else {}
    return {name.lower(): (name, str(number)) for name, number in saved.items()}


def _bridge(path: str, to: str, kind: str, payload: dict) -> dict:
    """One post to the bridge, or an error the model can speak. Nothing here names the number."""
    url, token = os.environ.get("ME3_PHONE_BRIDGE_URL", ""), os.environ.get("ME3_PHONE_BRIDGE_TOKEN", "")
    if not url or not token:
        return {"error": "the phone bridge is not set up"}
    found = _contacts().get(to.strip().lower())
    if not found:
        return {"error": f"{to} is not a saved contact"}
    name, number = found
    books = ledger()
    try:
        books.authorize("phone_bridge")
    except SpendRefused as e:
        return {"error": str(e)}
    try:
        r = httpx.post(f"{url.rstrip('/')}{path}", json=payload | {"number": number, "contact": name},
                       headers={"X-Bridge-Token": token}, timeout=BRIDGE_TIMEOUT_S, trust_env=False)  # fmt: skip
    except httpx.HTTPError as e:
        return {"error": f"the phone bridge did not answer: {e.__class__.__name__}"}
    if r.status_code != 200:
        return {"error": f"the phone bridge refused: {r.status_code}"}
    books.record("phone_bridge", kind, 1, price("phone_bridge_per_action"))
    return {"to": name, "status": r.json().get("status", "ok"), "dry_run": r.json().get("dry_run", False)}


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True}
)
def send_message(
    to: Annotated[str, Field(min_length=1, description="Contact name")],
    body: Annotated[str, Field(min_length=1, max_length=1000, description="The message text")],
) -> dict:
    """Send a message to a saved contact."""
    message = {"id": uuid.uuid4().hex[:8], "to": to, "body": body, "sent_at": time.time()}
    if tool_stage("comms") == "mock":
        _keep(message | {"kind": "message", "mock": True})
        return {"sent": True, "id": message["id"], "to": to, "mock": True}
    sent = _bridge("/message", to, "message", {"text": body})
    if "error" in sent:
        return sent
    _keep(message | sent | {"kind": "message"})
    return {"sent": not sent["dry_run"], "id": message["id"]} | sent


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True}
)
def call_contact(to: Annotated[str, Field(min_length=1, description="Contact name")]) -> dict:
    """Start a phone call to a saved contact. Someone must click Call on the Mac."""
    call = {"id": uuid.uuid4().hex[:8], "to": to, "body": "", "sent_at": time.time()}
    if tool_stage("comms") == "mock":
        _keep(call | {"kind": "call", "mock": True})
        return {"started": True, "id": call["id"], "to": to, "mock": True}
    placed = _bridge("/call", to, "call", {})
    if "error" in placed:
        return placed
    _keep(call | placed | {"kind": "call"})
    return {"started": not placed["dry_run"], "id": call["id"]} | placed


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
def list_contacts() -> dict:
    """List the names of saved contacts. Numbers stay private."""
    return {"contacts": sorted(name for name, _ in _contacts().values())}


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
def list_sent_messages() -> dict:
    """List messages and calls already sent, oldest first."""
    return {"messages": sorted(_load().values(), key=lambda m: m["sent_at"])}


if __name__ == "__main__":
    serve(mcp)
