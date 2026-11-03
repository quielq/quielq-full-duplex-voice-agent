"""A stuck MCP server must not hold up the exit (run 20261102-095054-8e433b took 62 s)."""

import asyncio
import time

from harness import mcp_tools


class StuckServer:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await asyncio.sleep(60)

    async def close(self):
        pass


async def test_a_stuck_server_is_left_behind(monkeypatch):
    monkeypatch.setattr(mcp_tools, "server_client", lambda *a: StuckServer())
    monkeypatch.setattr(mcp_tools, "CLOSE_TIMEOUT_S", 0.05)
    start = time.monotonic()
    async with mcp_tools.task_clients("stdio", servers={"clock": "x"}) as clients:
        assert "clock" in clients
    assert time.monotonic() - start < 5
