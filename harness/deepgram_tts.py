"""Deepgram Aura-2 TTS that discards audio from an interrupted reply.

The websocket carries no context ID, so Pipecat labels each chunk with whichever context
is active when it arrives. Chunks still in flight after an interruption land in the next
reply's context and play before that reply has any text. Everything before ``Cleared`` is
stale, so it is discarded and the next reply waits for ``Cleared``. Deepgram sometimes
never answers and keeps streaming the old reply; the socket is then replaced, since
nothing else separates the old audio from the new.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator, Callable

from loguru import logger

from pipecat.frames.frames import Frame
from pipecat.services.deepgram.tts import DeepgramTTSService

CLEAR_TIMEOUT_S = 0.5  # measured 227 ms when Deepgram honours Clear


class _FilteredSocket:
    """The websocket, with received messages passed through ``keep``."""

    def __init__(self, socket, keep: Callable[[str | bytes], bool]):
        self._socket = socket
        self._keep = keep

    def __getattr__(self, name):
        return getattr(self._socket, name)

    async def __aiter__(self):
        async for message in self._socket:
            if self._keep(message):
                yield message


class ClearAwareDeepgramTTSService(DeepgramTTSService):
    def __init__(self, *, on_event: Callable[..., object] | None = None, **kwargs):
        super().__init__(**kwargs)
        self._on_event = on_event or (lambda *a, **k: None)
        self._clears_pending = 0
        self._clear_sent_at = 0.0
        self._stale_chunks = 0
        self._last_stale_ms: int | None = None
        self._cleared = asyncio.Event()
        self._cleared.set()

    def _get_websocket(self):
        return _FilteredSocket(super()._get_websocket(), self._keep_message)

    async def on_audio_context_interrupted(self, context_id: str):
        if self._websocket:
            # Before the send: a reply can only follow it.
            self._clears_pending += 1
            self._clear_sent_at = time.monotonic()
            self._cleared.clear()
            self._on_event("tts_clear_sent")
        await super().on_audio_context_interrupted(context_id)

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        await self._wait_for_clean_socket()
        async for frame in super().run_tts(text, context_id):
            yield frame

    async def _wait_for_clean_socket(self) -> None:
        if self._cleared.is_set():
            return
        remaining = CLEAR_TIMEOUT_S - (time.monotonic() - self._clear_sent_at)
        try:
            await asyncio.wait_for(self._cleared.wait(), max(remaining, 0))
        except TimeoutError:
            logger.warning(f"{self} no Cleared within {CLEAR_TIMEOUT_S} s, replacing the socket")
            await self._drop_socket()
            self._finish_clear(timed_out=True)

    async def _drop_socket(self) -> None:
        # Not _disconnect(): that also closes the audio context of the reply about to speak.
        # run_tts reconnects when it finds no socket.
        if self._receive_task:
            await self.cancel_task(self._receive_task)
            self._receive_task = None
        await self._disconnect_websocket()

    def _keep_message(self, message: str | bytes) -> bool:
        if not self._clears_pending:
            return True
        if isinstance(message, bytes):
            self._stale_chunks += 1
            self._last_stale_ms = self._since_clear_ms()
            return False
        msg_type = self._message_type(message)
        if msg_type == "Cleared":
            self._clears_pending -= 1
            if not self._clears_pending:
                self._finish_clear(timed_out=False)
            return True
        # A stale Flushed would end the next reply's context. Warnings still get through.
        return msg_type != "Flushed"

    @staticmethod
    def _message_type(message: str) -> str | None:
        try:
            return json.loads(message).get("type")
        except json.JSONDecodeError:
            return None

    def _since_clear_ms(self) -> int:
        return round((time.monotonic() - self._clear_sent_at) * 1000)

    def _finish_clear(self, *, timed_out: bool) -> None:
        self._on_event(
            "tts_cleared",
            clear_ms=self._since_clear_ms(),
            stale_chunks=self._stale_chunks,
            last_stale_ms=self._last_stale_ms,
            timed_out=timed_out,
        )
        self._clears_pending = 0
        self._stale_chunks = 0
        self._last_stale_ms = None
        self._cleared.set()
