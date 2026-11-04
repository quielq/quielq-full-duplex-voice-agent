"""VoiceMCPBridge: the harness end of the voice servers' event stream (step 6).

Pipecat's ``MCPClient`` turns tools into LLM functions but has no hook for server
notifications, so the bridge talks to the voice servers through the raw MCP SDK
``ClientSession`` with a logging callback. Voice events (``servers/voice_events.py``) become
``VoiceEventFrame``s pushed into the pipeline, each stamped with the local MCP hop time.

Control calls (``speak``, ``stop``, ``duck``, ``unduck``, ``listen``, ``stop_listening``) are
methods on the bridge. ``speak`` and ``listen`` stay open while events stream, so they run
as background tasks and the bridge can call ``stop`` concurrently.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from pipecat.frames.frames import DataFrame, Frame, StartFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from harness.config import REPO_ROOT
from servers.voice_events import VOICE_LOGGER


@dataclass
class VoiceEventFrame(DataFrame):
    """A voice server event (speech_started, played_up_to, ...) delivered into the pipeline."""

    server: str = ""
    event: str = ""
    data: dict = field(default_factory=dict)
    hop_ms: float = 0.0  # server emit to bridge receipt, on the shared monotonic clock


@dataclass
class VoiceServer:
    name: str  # "speak" or "listen"
    module: str = ""  # stdio: python module to run
    url: str = ""  # http: streamable HTTP endpoint
    env: dict[str, str] | None = None  # stdio: the server's environment, None for the SDK default


class _Connection:
    """One ClientSession, owned by a dedicated task (MCP sessions are task-bound)."""

    def __init__(self, server: VoiceServer, on_event: Callable[[str, dict, float], Any]):
        self.server = server
        self._on_event = on_event
        self.session: ClientSession | None = None
        self._ready = asyncio.Event()
        self._closing = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._error: BaseException | None = None

    async def _logging_callback(self, params) -> None:
        if params.logger != VOICE_LOGGER or not isinstance(params.data, dict):
            return
        extra = params.data.get("extra") or {}
        hop_ms = (time.monotonic_ns() - extra.get("t_emit_ns", time.monotonic_ns())) / 1e6
        await self._on_event(self.server.name, extra, hop_ms)

    async def _run(self) -> None:
        try:
            if self.server.url:
                cm = streamable_http_client(self.server.url)
            else:
                cm = stdio_client(
                    StdioServerParameters(
                        command=sys.executable, args=["-m", self.server.module], cwd=str(REPO_ROOT),
                        env=self.server.env,
                    )
                )
            async with cm as (read, write):
                async with ClientSession(read, write, logging_callback=self._logging_callback) as s:
                    await s.initialize()
                    self.session = s
                    self._ready.set()
                    await self._closing.wait()
        except BaseException as e:  # surfaced to start()
            self._error = e
            self._ready.set()
        finally:
            self.session = None

    async def start(self, timeout: float = 20.0) -> None:
        self._task = asyncio.create_task(self._run())
        await asyncio.wait_for(self._ready.wait(), timeout)
        if self._error:
            raise RuntimeError(f"voice-{self.server.name}: {self._error}") from self._error

    async def close(self) -> None:
        self._closing.set()
        if self._task:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._task, 5)

    async def call(self, tool: str, args: dict | None = None) -> dict:
        if self.session is None:
            raise RuntimeError(f"voice-{self.server.name} is not connected")
        res = await self.session.call_tool(tool, args or {})
        return res.structured_content or {}


class VoiceMCPBridge(FrameProcessor):
    def __init__(
        self,
        speak: VoiceServer,
        listen: VoiceServer,
        *,
        on_event: Callable[..., object] | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._log = on_event or (lambda *a, **k: None)
        self._conns = {
            "speak": _Connection(speak, self._deliver),
            "listen": _Connection(listen, self._deliver),
        }
        self._calls: set[asyncio.Task] = set()
        self.events: list[VoiceEventFrame] = []
        self._started = False

    # Connection lifecycle -------------------------------------------------

    async def connect(self) -> None:
        for c in self._conns.values():
            await c.start()
        # Warm each session with a harmless call: the first tool call on a fresh stdio
        # session took 37 to 46 ms in testing, later ones about 3.5 ms (ADR-008).
        await self._conns["speak"].call("unduck")
        await self._conns["listen"].call("stop_listening")
        self._log("voice_bridge_connected", servers=list(self._conns))

    async def close(self) -> None:
        for t in list(self._calls):
            t.cancel()
        for c in self._conns.values():
            await c.close()

    async def cleanup(self):
        await super().cleanup()
        await self.close()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, StartFrame):
            self._started = True
        await self.push_frame(frame, direction)

    # Events ----------------------------------------------------------------

    async def _deliver(self, server: str, data: dict, hop_ms: float) -> None:
        event = data.get("event", "")
        payload = {k: v for k, v in data.items() if k not in ("event", "t_emit_ns")}
        frame = VoiceEventFrame(server=server, event=event, data=payload, hop_ms=hop_ms)
        self.events.append(frame)
        self._log(f"voice_{event}", hop_ms=round(hop_ms, 3), **payload)
        if self._started:
            await self.push_frame(frame)

    # Control calls -----------------------------------------------------------

    def _background(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._calls.add(task)
        task.add_done_callback(self._calls.discard)
        return task

    def speak(self, text: str, **kw) -> asyncio.Task:
        """Start playback; returns the open call as a task (resolves when playback ends)."""
        return self._background(self._conns["speak"].call("speak", {"text": text, **kw}))

    def listen(self, **kw) -> asyncio.Task:
        return self._background(self._conns["listen"].call("listen", kw))

    async def stop(self) -> dict:
        return await self._timed("speak", "stop")

    async def duck(self, gain: float = 0.3) -> dict:
        return await self._timed("speak", "duck", {"gain": gain})

    async def unduck(self) -> dict:
        return await self._timed("speak", "unduck")

    async def stop_listening(self) -> dict:
        return await self._timed("listen", "stop_listening")

    async def _timed(self, server: str, tool: str, args: dict | None = None) -> dict:
        t0 = time.monotonic_ns()
        res = await self._conns[server].call(tool, args)
        self._log("voice_call", server=server, tool=tool, rtt_ms=round((time.monotonic_ns() - t0) / 1e6, 3))
        return res
