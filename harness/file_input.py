"""Stream a mono 16-bit WAV into the pipeline in real time, as if from a microphone.

Paces 20 ms InputAudioRawFrames on the wall clock, with silence before and after so
the VAD and Smart Turn see a natural start and end. This is the seed of the
Full-Duplex-Bench adapter's file-based input.
"""

from __future__ import annotations

import asyncio
import time
import wave
from collections.abc import Callable
from pathlib import Path

from pipecat.frames.frames import CancelFrame, EndFrame, Frame, InputAudioRawFrame, StartFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class WavFileSource(FrameProcessor):
    def __init__(
        self,
        path: Path,
        *,
        lead_silence_s: float = 0.5,
        tail_silence_s: float = 3.0,
        on_event: Callable[..., object] | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        with wave.open(str(path), "rb") as w:
            if w.getnchannels() != 1 or w.getsampwidth() != 2:
                raise ValueError(f"{path}: need mono 16-bit PCM")
            self.sample_rate = w.getframerate()
            self._pcm = w.readframes(w.getnframes())
        self._lead = lead_silence_s
        self._tail = tail_silence_s
        self._on_event = on_event or (lambda *a, **k: None)
        self._task = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, (EndFrame, CancelFrame)) and self._task:
            # Stop feeding audio before the end reaches a streaming STT, or it reconnects and bills.
            await self.cancel_task(self._task)
            self._task = None
        await self.push_frame(frame, direction)
        if isinstance(frame, StartFrame) and self._task is None:
            self._task = self.create_task(self._stream())

    async def cleanup(self):
        await super().cleanup()
        if self._task:
            await self.cancel_task(self._task)
            self._task = None

    async def _stream(self) -> None:
        sr = self.sample_rate
        chunk = sr // 50 * 2  # 20 ms of 16-bit samples
        silence = b"\x00" * chunk
        lead = [silence] * int(self._lead * 50)
        speech = [self._pcm[i : i + chunk] for i in range(0, len(self._pcm), chunk)]
        tail = [silence] * int(self._tail * 50)

        t0 = time.monotonic()
        n = 0
        for part, chunks in (("lead", lead), ("speech", speech), ("tail", tail)):
            if part == "speech":
                self._on_event("injection_speech_start")
            if part == "tail":
                self._on_event("injection_speech_end")  # ground-truth end of user speech
            for c in chunks:
                await self.push_frame(InputAudioRawFrame(audio=c, sample_rate=sr, num_channels=1))
                n += 1
                await asyncio.sleep(max(0.0, t0 + n * 0.02 - time.monotonic()))
        self._on_event("injection_done")
