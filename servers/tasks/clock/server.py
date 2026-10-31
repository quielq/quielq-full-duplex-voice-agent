"""Clock MCP server: timers and alarms on a local scheduler with a persistent store.

Run over stdio (default, for local use) or streamable HTTP:

    uv run python -m servers.tasks.clock.server
    uv run python -m servers.tasks.clock.server --transport http --port 8765

State lives in $ME3_DATA_DIR/clock.json so a restart does not forget a timer.
Announcing a due timer through voice-speak comes with the voice servers (not built yet).
"""

from __future__ import annotations

import argparse
import json
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from servers.common import ToolCallLog, data_dir

mcp = FastMCP(
    "clock",
    instructions="Timers and alarms. Durations are in seconds; alarm times are 24-hour HH:MM local time.",
)
mcp.add_middleware(ToolCallLog("clock"))

_lock = threading.Lock()


def _store_path():
    return data_dir() / "clock.json"


def _load() -> dict:
    p = _store_path()
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def _save(items: dict) -> None:
    p = _store_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=2))
    tmp.replace(p)


def _view(item: dict, now: float) -> dict:
    remaining = item["fires_at"] - now
    return {
        "id": item["id"],
        "kind": item["kind"],
        "label": item["label"],
        "fires_at": datetime.fromtimestamp(item["fires_at"]).isoformat(timespec="seconds"),
        "remaining_seconds": max(0, round(remaining)),
        "status": "pending" if remaining > 0 else "done",
    }


def _add(kind: str, fires_at: float, label: str) -> dict:
    item = {"id": uuid.uuid4().hex[:8], "kind": kind, "label": label, "fires_at": fires_at}
    with _lock:
        items = _load()
        items[item["id"]] = item
        _save(items)
    return _view(item, time.time())


WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}
)
def get_time() -> dict:
    """Get the current local date and time. Always use this for the time or date; never guess."""
    now = datetime.now().astimezone()
    return {
        "iso": now.isoformat(timespec="seconds"),
        "time": now.strftime("%H:%M"),
        "spoken_time": now.strftime("%I:%M %p").lstrip("0"),
        "weekday": now.strftime("%A"),
        "date": now.strftime("%B %d, %Y"),
    }


@mcp.tool(annotations=WRITE)
def set_timer(
    duration_seconds: Annotated[int, Field(gt=0, le=86400, description="Timer length in seconds")],
    label: str = "",
) -> dict:
    """Start a countdown timer."""
    return _add("timer", time.time() + duration_seconds, label)


@mcp.tool(annotations=WRITE)
def set_alarm(
    time_hhmm: Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="24-hour HH:MM")],
    label: str = "",
) -> dict:
    """Set an alarm for the next occurrence of a local time."""
    hh, mm = map(int, time_hhmm.split(":"))
    now = datetime.now()
    at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if at <= now:
        at += timedelta(days=1)
    return _add("alarm", at.timestamp(), label)


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
def list_timers() -> dict:
    """List pending timers and alarms, soonest first."""
    # A dict, not a bare list: an empty list reaches MCPClient as no content, which it reports
    # to the LLM as a failed call.
    now = time.time()
    items = sorted(_load().values(), key=lambda i: i["fires_at"])
    return {"timers": [_view(i, now) for i in items if i["fires_at"] > now]}


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False}
)
def cancel_timer(timer_id: str) -> dict:
    """Cancel a timer or alarm by id. Get the id from list_timers first; never ask the user for it."""
    with _lock:
        items = _load()
        item = items.pop(timer_id, None)
        _save(items)
    if item is None:
        return {"cancelled": False, "reason": f"no timer with id {timer_id}"}
    return {"cancelled": True, **_view(item, time.time())}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    if args.transport == "stdio":
        mcp.run(transport="stdio", show_banner=False)
    else:
        mcp.run(transport="http", host=args.host, port=args.port, show_banner=False)


if __name__ == "__main__":
    main()
