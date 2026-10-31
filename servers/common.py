"""Shared pieces for our FastMCP servers: tool-call logging, data paths, and startup."""

from __future__ import annotations

import argparse
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


def serve(mcp) -> None:
    """Run a FastMCP server over stdio (default) or streamable HTTP, from the command line."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    if args.transport == "stdio":
        mcp.run(transport="stdio", show_banner=False)
    else:
        mcp.run(transport="http", host=args.host, port=args.port, show_banner=False)
