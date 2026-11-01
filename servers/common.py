"""Shared pieces for our FastMCP servers: tool-call logging, data paths, and startup."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import yaml
from fastmcp.server.middleware import Middleware, MiddlewareContext

from bench.cost_ledger import CostLedger

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


FAKE_TOOL_STAGES = "search=fake,weather=fake,fetch=fake,comms=mock"  # tests and the task suite spend nothing


def tool_stage(name: str) -> str:
    """Which backend a tool uses (ADR-021). Read here so a server needs no harness import."""
    for item in filter(None, os.environ.get("ME3_TOOL_STAGES", "").split(",")):
        stage, _, choice = item.partition("=")
        if stage == name:
            return choice
    return _providers()["tool_stages"][name]


def ledger() -> CostLedger:
    """The bot's ledger, so tool spend counts toward the same daily caps."""
    return CostLedger.from_providers_config(_providers(), data_dir() / "cost_ledger.jsonl")


def price(name: str) -> float:
    return float(_providers()["prices"][name])


def _providers() -> dict:
    return yaml.safe_load((REPO_ROOT / "configs" / "providers.yaml").read_text())


def workspace_dir() -> Path:
    """The only place tools may write."""
    d = data_dir() / "workspace"
    d.mkdir(exist_ok=True)
    return d


def documents_dir() -> Path:
    """Read-only documents for search and reading."""
    d = data_dir() / "documents"
    d.mkdir(exist_ok=True)
    return d


class OutsideRoots(ValueError):
    """A path resolves outside the roots a tool may use."""


def inside(path: str, roots: list[Path]) -> Path:
    """Resolve a path, following symlinks, and refuse it unless it stays in one of the roots.
    A relative path starts from the first root."""
    resolved_roots = [r.resolve() for r in roots]
    target = (resolved_roots[0] / path).resolve()
    if any(target.is_relative_to(r) for r in resolved_roots):
        return target
    raise OutsideRoots(f"{path} is outside the allowed folders")


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
