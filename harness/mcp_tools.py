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

CLOSE_TIMEOUT_S = 5.0
CLOCK_MODULE = "servers.tasks.clock.server"
TASK_SERVERS = {
    "clock": CLOCK_MODULE,
    "notes": "servers.tasks.notes.server",
    "thermostat": "servers.tasks.thermostat.server",
    "comms": "servers.tasks.comms.server",
    "lights": "servers.tasks.lights.server",
    "music": "servers.tasks.music.server",
    "knowledge": "servers.tasks.knowledge.server",
    "docs": "servers.tasks.docs.server",
    "workspace": "servers.tasks.workspace.server",
    "delegate": "servers.tasks.delegate.server",
}
VOICE_SERVERS = {"listen": "servers.voice_listen.server", "speak": "servers.voice_speak.server"}
# The only exceptions: search (ADR-021), each voice server's own provider (ADR-032), and the
# delegate worker's model, which is a fake unless ME3_TOOL_STAGES=delegate=anthropic (ADR-035).
SERVER_KEYS = {
    "servers.tasks.knowledge.server": {"TAVILY_API_KEY"},
    VOICE_SERVERS["listen"]: {"DEEPGRAM_API_KEY"},
    VOICE_SERVERS["speak"]: {"CARTESIA_API_KEY"},
    TASK_SERVERS["delegate"]: {"ANTHROPIC_API_KEY"},
}


def _server_env(data_dir: str | None, module: str = "", extra: dict[str, str] | None = None) -> dict[str, str]:
    allowed = SERVER_KEYS.get(module, set())
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY") or k in allowed}
    if module != TASK_SERVERS["music"]:  # Spotify credentials reach only music (ADR-022)
        env = {k: v for k, v in env.items() if not k.startswith("SPOTIFY_")}
    if module != TASK_SERVERS["comms"]:  # the bridge token reaches only comms (ADR-024)
        env.pop("ME3_PHONE_BRIDGE_TOKEN", None)
    if data_dir:
        env["ME3_DATA_DIR"] = data_dir
    env.pop("ME3_DOC_SCOPE", None)  # set only by an agent, below, never inherited
    env.update(extra or {})
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
            raise RuntimeError(f"MCP HTTP server exited with code {proc.returncode}")
        with contextlib.suppress(OSError):
            _, w = await asyncio.open_connection("127.0.0.1", port)
            w.close()
            return
        await asyncio.sleep(0.1)
    raise TimeoutError(f"MCP HTTP server did not open port {port}")


@contextlib.asynccontextmanager
async def http_server(
    module: str, data_dir: str | None = None, extra: dict[str, str] | None = None
) -> AsyncIterator[str]:
    """Start one of our FastMCP servers over streamable HTTP on loopback; yield its URL."""
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", module, "--transport", "http", "--port", str(port)],
        cwd=REPO_ROOT,
        env=_server_env(data_dir, module, extra),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        await _wait_for_port(port, proc)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@contextlib.asynccontextmanager
async def clock_http_client(data_dir: str | None = None) -> AsyncIterator[MCPClient]:
    """Start the clock server over streamable HTTP on loopback and yield a client for it."""
    async with http_server(CLOCK_MODULE, data_dir) as url:
        yield MCPClient(server_params=StreamableHttpParameters(url=url))


@contextlib.asynccontextmanager
async def clock_client(transport: str, data_dir: str | None = None) -> AsyncIterator[MCPClient]:
    async with server_client(CLOCK_MODULE, transport, data_dir) as client:
        yield client


@contextlib.asynccontextmanager
async def server_client(
    module: str, transport: str, data_dir: str | None = None, extra: dict[str, str] | None = None
) -> AsyncIterator[MCPClient]:
    if transport == "stdio":
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", module],
            cwd=str(REPO_ROOT),
            env=_server_env(data_dir, module, extra),
        )
        yield MCPClient(server_params=params)
    elif transport == "http":
        async with http_server(module, data_dir, extra) as url:
            yield MCPClient(server_params=StreamableHttpParameters(url=url))
    else:
        raise ValueError(f"unknown MCP transport {transport!r}")


@contextlib.asynccontextmanager
async def task_clients(
    transport: str,
    data_dir: str | None = None,
    servers: dict[str, str] | None = None,
    doc_scope: list[str] | None = None,
) -> AsyncIterator[dict[str, MCPClient]]:
    """One client per task server, keyed by server name. ``doc_scope`` limits the docs server
    to those folders under data/documents (ADR-039)."""
    clients: dict[str, MCPClient] = {}
    entered = []
    try:
        for name, module in (servers or TASK_SERVERS).items():
            extra = {"ME3_DOC_SCOPE": ",".join(doc_scope)} if doc_scope and module == TASK_SERVERS["docs"] else None
            cm = server_client(module, transport, data_dir, extra)
            clients[name] = await cm.__aenter__()
            entered.append(cm)
        yield clients
    finally:
        # A stuck server must not hold up the exit. Its stdin closes with this process.
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(CLOSE_TIMEOUT_S):
                for client in clients.values():
                    await client.close()
                for cm in reversed(entered):
                    await cm.__aexit__(None, None, None)
