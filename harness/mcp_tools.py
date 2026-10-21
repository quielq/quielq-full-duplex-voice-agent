"""Connect task MCP servers to the LLM through Pipecat's MCPClient.

Pipecat 1.13 deprecates ``MCPClient.register_tools(llm)`` (since 1.8.0). The supported
path is ``await client.tools()`` passed to ``LLMContext(tools=...)``; handlers then
register automatically. See docs/decisions/ADR-003-mcp-tool-registration.md.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import subprocess
import sys
from collections.abc import AsyncIterator

from mcp import StdioServerParameters
from mcp.client.session_group import StreamableHttpParameters
from pipecat.services.mcp_service import MCPClient

from harness.config import REPO_ROOT

CLOCK_MODULE = "servers.tasks.clock.server"


def _server_env(data_dir: str | None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}  # servers get no keys
    if data_dir:
        env["ME3_DATA_DIR"] = data_dir
    return env


def clock_stdio_client(data_dir: str | None = None) -> MCPClient:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", CLOCK_MODULE],
        cwd=str(REPO_ROOT),
        env=_server_env(data_dir),
    )
    return MCPClient(server_params=params)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _wait_for_port(port: int, proc: subprocess.Popen, timeout: float = 20.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"clock HTTP server exited with code {proc.returncode}")
        with contextlib.suppress(OSError):
            _, w = await asyncio.open_connection("127.0.0.1", port)
            w.close()
            return
        await asyncio.sleep(0.1)
    raise TimeoutError(f"clock HTTP server did not open port {port}")


@contextlib.asynccontextmanager
async def clock_http_client(data_dir: str | None = None) -> AsyncIterator[MCPClient]:
    """Start the clock server over streamable HTTP on loopback and yield a client for it."""
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", CLOCK_MODULE, "--transport", "http", "--port", str(port)],
        cwd=REPO_ROOT,
        env=_server_env(data_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        await _wait_for_port(port, proc)
        yield MCPClient(server_params=StreamableHttpParameters(url=f"http://127.0.0.1:{port}/mcp"))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@contextlib.asynccontextmanager
async def clock_client(transport: str, data_dir: str | None = None) -> AsyncIterator[MCPClient]:
    if transport == "stdio":
        yield clock_stdio_client(data_dir)
    elif transport == "http":
        async with clock_http_client(data_dir) as client:
            yield client
    else:
        raise ValueError(f"unknown MCP transport {transport!r}")
