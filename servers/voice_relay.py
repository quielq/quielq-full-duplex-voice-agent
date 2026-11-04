"""The shared core of the voice servers (ADR-032): hold a provider stream, relay it locally.

A voice server owns the provider connection and its key. The harness never sees the key: it
asks for a stream with an MCP call (``listen`` or ``speak``) that stays open while the stream
lives, and reaches the stream itself on a Unix socket in a private folder. MCP carries the
control and the events. The socket carries the provider's own messages, audio included.

The upstream address is fixed per server. Only its query string comes from the harness, so a
caller cannot point the server, or its key, anywhere else. Tests may name a loopback upstream
with an environment variable, and nothing else.

Socket frames: one kind byte (``B`` binary, ``T`` text), a 4-byte big-endian length, the payload.
The first frame from the harness is the session token, as text.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import secrets
import struct
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import websockets
import yaml
from fastmcp import Context

from bench.cost_ledger import CostLedger, SpendRefused
from servers.common import REPO_ROOT, data_dir
from servers.voice_events import emit

HEADER = struct.Struct(">cI")
MAX_FRAME = 4 * 1024 * 1024
CONNECT_TIMEOUT_S = 10.0
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


async def read_frame(reader: asyncio.StreamReader) -> tuple[bytes, bytes]:
    kind, size = HEADER.unpack(await reader.readexactly(HEADER.size))
    if size > MAX_FRAME:
        raise ValueError(f"frame of {size} bytes is too large")
    return kind, await reader.readexactly(size)


def write_frame(writer: asyncio.StreamWriter, message: str | bytes) -> None:
    payload, kind = (message.encode(), b"T") if isinstance(message, str) else (bytes(message), b"B")
    writer.write(HEADER.pack(kind, len(payload)) + payload)


def _ledger() -> CostLedger:
    cfg = yaml.safe_load((REPO_ROOT / "configs" / "providers.yaml").read_text())
    return CostLedger.from_providers_config(cfg, data_dir() / "cost_ledger.jsonl")


@dataclass
class Upstream:
    """Where a server's provider lives, and how to sign in. The key never leaves this process."""

    provider: str            # cost ledger name
    url: str                 # fixed; the harness adds only a query string
    env_key: str
    auth: Callable[[str], dict[str, str]]
    test_env: str            # a loopback URL for tests, and nothing else
    events: Callable[[dict], tuple[str, dict] | None] = lambda message: None
    headers: frozenset[str] = frozenset()  # headers the harness may set, such as a version


@dataclass
class _Session:
    sid: str
    path: Path
    token: str
    done: asyncio.Event = field(default_factory=asyncio.Event)
    reason: str = ""


class Relay:
    def __init__(self, name: str, upstream: Upstream, ledger: Callable[[], CostLedger] = _ledger):
        self._name = name
        self._up = upstream
        self._ledger = ledger
        self._dir = Path(tempfile.mkdtemp(prefix=f"me3-{name}-"))
        os.chmod(self._dir, 0o700)
        self._sessions: dict[str, _Session] = {}

    def _url(self, query: str) -> str:
        if "#" in query or "/" in query.split("=", 1)[0]:
            raise ValueError("the query string must be key=value pairs")
        base = os.environ.get(self._up.test_env) or self._up.url
        if base != self._up.url and urlsplit(base).hostname not in LOOPBACK:
            raise ValueError(f"{self._up.test_env} may only name this machine")
        return f"{base}?{query}" if query else base

    async def open(self, ctx: Context, query: str = "", headers: dict | None = None) -> dict:
        """Open one provider stream and relay it until either end closes. Blocks while it lives."""
        extra = {k: str(v) for k, v in (headers or {}).items() if k in self._up.headers}
        try:
            url = self._url(query)
            self._ledger().authorize(self._up.provider)
            key = os.environ.get(self._up.env_key, "")
            if not key:
                raise SpendRefused(f"{self._up.provider}: {self._up.env_key} is not set")
        except (SpendRefused, ValueError) as e:
            await emit(ctx, "stream_refused", reason=str(e))
            return {"opened": False, "error": str(e)}

        sid = secrets.token_hex(4)
        session = _Session(sid, self._dir / f"{sid}.sock", secrets.token_hex(16))
        self._sessions[sid] = session
        ready = asyncio.Event()

        async def on_local(reader, writer):
            if ready.is_set():  # one harness connection per stream
                writer.close()
                return
            try:
                kind, token = await asyncio.wait_for(read_frame(reader), CONNECT_TIMEOUT_S)
                if kind != b"T" or not secrets.compare_digest(token.decode(), session.token):
                    raise PermissionError("wrong session token")
            except (Exception, asyncio.CancelledError):
                writer.close()
                return
            ready.set()
            await self._pump(ctx, session, url, {**self._up.auth(key), **extra}, reader, writer)

        server = await asyncio.start_unix_server(on_local, path=str(session.path))
        os.chmod(session.path, 0o600)
        try:
            await emit(ctx, "stream_ready", session=sid, path=str(session.path), token=session.token)
            try:
                await asyncio.wait_for(ready.wait(), CONNECT_TIMEOUT_S)
            except TimeoutError:
                session.reason = "the harness did not connect"
            else:
                await session.done.wait()
        finally:
            server.close()
            with contextlib.suppress(FileNotFoundError):
                session.path.unlink()
            self._sessions.pop(sid, None)
        await emit(ctx, "stream_closed", session=sid, reason=session.reason)
        return {"opened": True, "session": sid, "reason": session.reason}

    async def _pump(self, ctx, session, url, headers, reader, writer) -> None:
        t0 = time.monotonic()
        try:
            async with websockets.connect(url, additional_headers=headers, open_timeout=CONNECT_TIMEOUT_S) as ws:
                await emit(ctx, "upstream_connected", session=session.sid, connect_ms=round((time.monotonic() - t0) * 1000))

                async def down():  # provider to harness
                    async for message in ws:
                        write_frame(writer, message)
                        await writer.drain()
                        if isinstance(message, str) and (named := self._event(message)):
                            await emit(ctx, named[0], session=session.sid, **named[1])

                async def up():  # harness to provider
                    while True:
                        kind, payload = await read_frame(reader)
                        await ws.send(payload.decode() if kind == b"T" else payload)

                tasks = [asyncio.create_task(down()), asyncio.create_task(up())]
                stopper = asyncio.create_task(session.done.wait())
                finished, _ = await asyncio.wait([*tasks, stopper], return_when=asyncio.FIRST_COMPLETED)
                for task in [*tasks, stopper]:
                    task.cancel()
                if not session.reason:
                    session.reason = "stopped" if stopper in finished else _why(finished)
        except Exception as e:
            session.reason = f"upstream: {type(e).__name__}: {e}"[:200]
        finally:
            writer.close()
            session.done.set()

    def _event(self, message: str) -> tuple[str, dict] | None:
        try:
            return self._up.events(json.loads(message))
        except (ValueError, TypeError, AttributeError):
            return None

    def stop(self, session: str = "") -> dict:
        """Close one stream, or every stream when no session is named."""
        stopped = [s.sid for s in self._sessions.values() if not session or s.sid == session]
        for sid in stopped:
            self._sessions[sid].reason = "stopped"
            self._sessions[sid].done.set()
        return {"stopped": stopped}

    def status(self) -> dict:
        return {"server": self._name, "provider": self._up.provider, "streams": len(self._sessions)}


def _why(finished: set[asyncio.Task]) -> str:
    for task in finished:
        if task.cancelled():
            continue
        if (error := task.exception()) is None:
            return "provider closed the stream"
        if isinstance(error, asyncio.IncompleteReadError):
            return "harness closed the stream"
        return f"{type(error).__name__}: {error}"[:200]
    return "closed"
