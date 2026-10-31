"""Deepgram Aura-2 TTS that opens a fresh socket on every interruption (ADR-012).

The websocket carries no context ID, so Pipecat labels each chunk with whichever context
is active when it arrives. Chunks still in flight after an interruption land in the next
reply's context and play before that reply has any text. Deepgram cannot be relied on to
stop: live, it streamed for up to 1.8 s after ``Clear`` and often never sent ``Cleared``.
Dropping the socket ends the old reply for certain.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, Callable

from loguru import logger

from harness.audio_hops import HopTimedTTS
from pipecat.frames.frames import Frame
from pipecat.services.deepgram.tts import DeepgramTTSService


class FreshSocketDeepgramTTSService(HopTimedTTS, DeepgramTTSService):
    def __init__(self, *, on_event: Callable[..., object] | None = None, **kwargs):
        super().__init__(**kwargs)
        self._on_event = on_event or (lambda *a, **k: None)
        self._reopening: asyncio.Task | None = None

    async def on_audio_context_interrupted(self, context_id: str):
        await super().on_audio_context_interrupted(context_id)
        if not self._websocket:
            return  # already reopening, for this reply's other context
        old = self._websocket
        self._websocket = None
        if self._receive_task:
            await self.cancel_task(self._receive_task)
            self._receive_task = None
        self.create_task(self._close(old))
        self._reopening = self.create_task(self._reopen())

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        if self._reopening:
            # Shielded: an interruption here must not abandon the connection.
            await asyncio.shield(self._reopening)
        async for frame in super().run_tts(text, context_id):
            yield frame

    async def _reopen(self) -> None:
        start = time.monotonic()
        await self._connect()
        self._reopening = None
        self._on_event("tts_socket_reopened", reopen_ms=round((time.monotonic() - start) * 1000))

    @staticmethod
    async def _close(socket) -> None:
        # In the background: Deepgram can take the full 2 s close timeout.
        try:
            await socket.close()
        except Exception as e:
            logger.debug(f"closing the interrupted TTS socket: {e}")
