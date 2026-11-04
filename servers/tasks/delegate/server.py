"""Delegate MCP server: hand a longer job to a background worker (ADR-035).

``start_task`` returns at once with an id. The worker (worker.py) runs a short tool loop on
Haiku 5.5, or on a scripted fake by default, using only what needs no spoken yes. State lives
in $ME3_DATA_DIR/delegate.json, which harness/delegate_watch.py reads to announce a finished
task at the next quiet moment, the way timer_watch.py announces timers.

The model is a fake unless ME3_TOOL_STAGES=delegate=anthropic, and then every call passes the
``anthropic`` cap in bench/cost_ledger.py.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
import uuid
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from bench.cost_ledger import SpendRefused
from servers.common import ToolCallLog, _providers, data_dir, ledger, serve, tool_stage
from servers.tasks.delegate import worker
from servers.tasks.delegate.worker import CANCELLED, FAILED, RUNNING, AnthropicModel, FakeModel

mcp = FastMCP("delegate", instructions="Run a longer job in the background and report when it is done.")
mcp.add_middleware(ToolCallLog("delegate"))

MAX_RUNNING = 2
TASK_ID = Field(pattern=r"^t[0-9a-f]{6}$", description="The id start_task returned")

_tasks: dict[str, dict] = {}
_jobs: dict[str, asyncio.Task] = {}
_loaded = False


def _path():
    return data_dir() / "delegate.json"


def _load() -> None:
    """Keep finished results across restarts. A task that was running when the server stopped
    cannot resume, so it is marked failed, quietly: it finished before this session."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        old = json.loads(_path().read_text())
    except (OSError, ValueError):
        return
    for t in old.values():
        if t["state"] == RUNNING:
            t |= {"state": FAILED, "result": "The assistant restarted before it finished.",
                  "finished_at": t["started_at"]}  # fmt: skip
    _tasks.update(old)


def _save() -> None:
    tmp = _path().with_suffix(".tmp")
    tmp.write_text(json.dumps(_tasks))
    os.replace(tmp, _path())  # the watcher never reads half a file


def make_model():
    """The fake unless the stage says otherwise. Tests replace this."""
    if tool_stage("delegate") == "anthropic":
        cfg = _providers()
        p = cfg["providers"]["anthropic"]
        return AnthropicModel(ledger(), p["model"], p.get("max_tokens", 8192), cfg["prices"]["anthropic"])
    return FakeModel()


async def _run(task_id: str, model) -> None:
    task = _tasks[task_id]

    def on_step(step: dict) -> None:
        task["steps"] = task.get("steps", 0) + 1
        task["last_tool"] = step["tool"]
        _save()

    try:
        out = await worker.run_task(task["goal"], model, on_step)
        task |= {"state": out.state, "result": out.result, "model_calls": out.steps, "usd": round(out.usd, 6)}
    except asyncio.CancelledError:
        task |= {"state": CANCELLED, "result": "Cancelled."}
        raise
    except Exception as e:  # a worker bug must end the task, not leave it running forever
        task |= {"state": FAILED, "result": f"It failed: {type(e).__name__}"}
    finally:
        task["finished_at"] = time.time()
        _jobs.pop(task_id, None)
        _save()


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True}
)
async def start_task(
    goal: Annotated[str, Field(min_length=3, max_length=2000, description="What to do, in full")],
) -> dict:
    """Start a longer job in the workspace in the background, such as writing a few files. Returns an id at once, and the result is announced when done. It cannot run commands that need a yes."""
    _load()
    if sum(t["state"] == RUNNING for t in _tasks.values()) >= MAX_RUNNING:
        return {"error": f"{MAX_RUNNING} background tasks are already running. Wait for one, or cancel one."}
    model = make_model()
    if isinstance(model, AnthropicModel):
        try:
            ledger().authorize(worker.PROVIDER)  # refuse now, not after the user has walked away
        except SpendRefused as e:
            return {"error": str(e)}
    task_id = "t" + uuid.uuid4().hex[:6]
    _tasks[task_id] = {"id": task_id, "goal": goal, "state": RUNNING, "result": "", "steps": 0,
                       "started_at": time.time(), "finished_at": None}  # fmt: skip
    _save()
    _jobs[task_id] = asyncio.create_task(_run(task_id, model))
    return {"task_id": task_id, "state": RUNNING, "note": "Started. I will say when it is done."}


def _find(task_id: str) -> dict | None:
    _load()
    return _tasks.get(task_id)


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
def task_status(task_id: Annotated[str, TASK_ID]) -> dict:
    """Say whether a background task is still running, and how far it has got."""
    if not (t := _find(task_id)):
        return {"error": f"no task called {task_id}"}
    return {k: t.get(k) for k in ("id", "goal", "state", "steps", "last_tool")}


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
def task_result(task_id: Annotated[str, TASK_ID]) -> dict:
    """Get what a finished background task did."""
    if not (t := _find(task_id)):
        return {"error": f"no task called {task_id}"}
    if t["state"] == RUNNING:
        return {"id": task_id, "state": RUNNING, "note": "Still running."}
    return {k: t.get(k) for k in ("id", "goal", "state", "result", "steps")}


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False}
)
async def cancel_task(task_id: Annotated[str, TASK_ID]) -> dict:
    """Stop a running background task. Files it already wrote stay, with old versions kept."""
    if not (t := _find(task_id)):
        return {"error": f"no task called {task_id}"}
    if t["state"] != RUNNING:
        return {"id": task_id, "state": t["state"], "note": "It had already finished."}
    job = _jobs.get(task_id)
    if job:
        job.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await job
    else:  # no job in this process: mark it so it never looks alive
        t |= {"state": CANCELLED, "result": "Cancelled.", "finished_at": time.time()}
        _save()
    return {"id": task_id, "state": CANCELLED}


if __name__ == "__main__":
    serve(mcp)
