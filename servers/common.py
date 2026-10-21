"""Shared pieces for our FastMCP servers: tool-call logging and data paths."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from fastmcp.server.middleware import Middleware, MiddlewareContext

REPO_ROOT = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Where servers keep state. Overridable so tests never touch real data."""
    d = Path(os.environ.get("ME3_DATA_DIR", REPO_ROOT / "data"))
    d.mkdir(parents=True, exist_ok=True)
    return d


class ToolCallLog(Middleware):
    """Log every tool call with timestamp, arguments, result, and duration (one JSON line each)."""

    def __init__(self, server_name: str):
        self._server = server_name

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        start_ns = time.monotonic_ns()
        params = context.message
        rec = {
            "ts": time.time(),
            "server": self._server,
            "tool": params.name,
            "args": params.arguments,
        }
        try:
            result = await call_next(context)
            rec["result"] = getattr(result, "structured_content", None) or str(result)
            rec["ok"] = True
            return result
        except Exception as e:
            rec["ok"] = False
            rec["error"] = str(e)
            raise
        finally:
            rec["duration_ms"] = (time.monotonic_ns() - start_ns) / 1e6
            with open(data_dir() / "tool_calls.jsonl", "a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
