"""The harness end of the voice servers (ADR-032): Pipecat's own Flux and Cartesia services,
with their provider connection swapped for a stream from our MCP voice server.

Pipecat opens each provider WebSocket through ``_websocket_connect``. The mixin here answers
that call with an MCP ``listen`` or ``speak`` call to the voice server and returns a stand-in
WebSocket over the stream's Unix socket. Everything above the socket stays Pipecat's: turn
events, the watchdog, reconnects, word timestamps, and cancelling a reply on interruption.
The harness holds no provider key, and the cost ledger still meters from the same frames.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from urllib.parse import urlsplit

from websockets.protocol import State

from harness.mcp_tools import VOICE_SERVERS, _server_env, http_server
from harness.voice_bridge import VoiceServer, _Connection
from servers.voice_relay import read_frame, write_frame

READY_TIMEOUT_S = 15.0
HIDDEN = {"token", "path"}  # never logged


class RelayedWebSocket:
    """Just enough of a websockets client connection for Pipecat's Flux and Cartesia services."""

    response = None

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, call: asyncio.Task):
        self._reader = reader
        self._writer = writer
        self._call = call
        self.state = State.OPEN

    async def send(self, message: str | bytes) -> None:
        if self.state is not State.OPEN:
            raise ConnectionError("the voice stream is closed")
        write_frame(self._writer, message)
        await self._writer.drain()

    async def recv(self) -> str | bytes:
        try:
            kind, payload = await read_frame(self._reader)
        except (asyncio.IncompleteReadError, ConnectionError):
            self.state = State.CLOSED
            raise
        return payload.decode() if kind == b"T" else payload

    def __aiter__(self):
        return self

    async def __anext__(self) -> str | bytes:
        try:
            return await self.recv()
        except (asyncio.IncompleteReadError, ConnectionError):
            raise StopAsyncIteration from None

    async def ping(self) -> None:
        if self.state is not State.OPEN:
            raise ConnectionError("the voice stream is closed")

    async def close(self) -> None:
        if self.state is State.CLOSED:
            return
        self.state = State.CLOSED
        self._writer.close()
        with contextlib.suppress(Exception):
            await self._writer.wait_closed()
        # The MCP call ends once the server sees the socket close.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(asyncio.shield(self._call), 2.0)


class RelayClient:
    """One MCP session to one voice server, opened on first use and shared by its streams."""

    def __init__(self, name: str, transport: str = "stdio", data_dir: str | None = None,
                 on_event: Callable[..., object] | None = None):  # fmt: skip
        self.name = name
        self._module = VOICE_SERVERS[name]
        self._transport = transport
        self._data_dir = data_dir
        self._log = on_event or (lambda *a, **k: None)
        self._conn: _Connection | None = None
        self._stack = contextlib.AsyncExitStack()
        self._lock = asyncio.Lock()
        self._ready: asyncio.Future | None = None
        self._calls: set[asyncio.Task] = set()

    async def _on_event(self, server: str, data: dict, hop_ms: float) -> None:
        event = data.get("event", "")
        if event == "stream_ready" and self._ready and not self._ready.done():
            self._ready.set_result(data)
        shown = {k: v for k, v in data.items() if k not in HIDDEN | {"event", "t_emit_ns"}}
        self._log(f"voice_{event}", server=self.name, hop_ms=round(hop_ms, 3), **shown)

    async def _connect(self) -> _Connection:
        if self._conn is None:
            if self._transport == "http":
                url = await self._stack.enter_async_context(http_server(self._module, self._data_dir))
                server = VoiceServer(self.name, url=url)
            else:
                server = VoiceServer(self.name, module=self._module, env=_server_env(self._data_dir, self._module))
            conn = _Connection(server, self._on_event)
            await conn.start()
            self._conn = conn
        return self._conn

    async def open(self, tool: str, args: dict) -> RelayedWebSocket:
        """Ask the server for a stream and connect to its socket."""
        async with self._lock:
            conn = await self._connect()
            self._ready = asyncio.get_running_loop().create_future()
            call = asyncio.create_task(conn.call(tool, args))
            self._calls.add(call)
            call.add_done_callback(self._calls.discard)
            done, _ = await asyncio.wait({self._ready, call}, timeout=READY_TIMEOUT_S,
                                         return_when=asyncio.FIRST_COMPLETED)  # fmt: skip
            if self._ready not in done:
                result = call.result() if call in done and not call.exception() else {}
                call.cancel()
                raise ConnectionError(f"voice-{self.name}: {result.get('error') or 'no stream'}")
            ready = self._ready.result()
            reader, writer = await asyncio.open_unix_connection(ready["path"])
            write_frame(writer, ready["token"])
            await writer.drain()
            return RelayedWebSocket(reader, writer, call)

    async def close(self) -> None:
        for call in list(self._calls):
            call.cancel()
        if self._conn:
            await self._conn.close()
            self._conn = None
        await self._stack.aclose()


class RelayedConnection:
    """Mixin for a Pipecat WebSocket service: its provider socket comes from a voice server."""

    def __init__(self, *, relay: RelayClient, relay_tool: str, **kwargs):
        super().__init__(**kwargs)
        self._relay = relay
        self._relay_tool = relay_tool

    async def _websocket_connect(self, uri: str, **kwargs):
        args = {"query": urlsplit(uri).query}
        if version := (kwargs.get("additional_headers") or {}).get("Cartesia-Version"):
            args["version"] = version
        return await self._relay.open(self._relay_tool, args)

    async def cleanup(self):
        await super().cleanup()
        await self._relay.close()
