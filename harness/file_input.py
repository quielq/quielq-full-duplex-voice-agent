"""A scripted microphone: WAV segments streamed in real time, silence in between.

Paces 20 ms InputAudioRawFrames on the wall clock, like a sound card, until the
pipeline ends. Each segment starts either a fixed delay after the previous one, or a
fixed delay after the bot starts speaking (to script a barge-in). This is the seed
of the Full-Duplex-Bench adapter's file-based input.
"""

from __future__ import annotations

import asyncio
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    CancelFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    StartFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


@dataclass
class Segment:
    path: Path
    after: str = "previous"  # "previous" segment end, or "bot_speaking"
    delay_s: float = 0.5


def speech_onset_s(pcm: bytes, sample_rate: int, threshold: int = 500) -> float:
    """Seconds of leading near-silence before the first loud sample (TTS files start with a pad)."""
    import array

    samples = array.array("h", pcm)
    for i, v in enumerate(samples):
        if abs(v) >= threshold:
            return i / sample_rate
    return 0.0


def read_pcm(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(f"{path}: need mono 16-bit PCM")
        return w.readframes(w.getnframes()), w.getframerate()


class ScriptedMicSource(FrameProcessor):
    def __init__(
        self,
        segments: list[Segment],
        *,
        max_s: float = 120.0,
        on_event: Callable[..., object] | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._segments = [(s, *read_pcm(s.path)) for s in segments]
        rates = {sr for _, _, sr in self._segments}
        if len(rates) != 1:
            raise ValueError(f"segments must share one sample rate, got {rates}")
        self.sample_rate = rates.pop()
        self._max_s = max_s
        self._on_event = on_event or (lambda *a, **k: None)
        self._task = None
        self._bot_speaking = asyncio.Event()
        # Set once the last segment has been fully injected.
        self.all_injected = asyncio.Event()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking.set()
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
        t0 = time.monotonic()
        n = 0

        async def send(c: bytes) -> None:
            nonlocal n
            await self.push_frame(InputAudioRawFrame(audio=c, sample_rate=sr, num_channels=1))
            n += 1
            await asyncio.sleep(max(0.0, t0 + n * 0.02 - time.monotonic()))

        async def silence_until(deadline: float) -> None:
            while time.monotonic() < deadline:
                await send(silence)

        for i, (seg, pcm, _) in enumerate(self._segments):
            if seg.after == "bot_speaking":
                self._bot_speaking.clear()
                while not self._bot_speaking.is_set():
                    await send(silence)
            await silence_until(time.monotonic() + seg.delay_s)
            self._on_event("injection_speech_start", segment=i,
                           onset_offset_s=round(speech_onset_s(pcm, sr), 3))  # fmt: skip
            for j in range(0, len(pcm), chunk):
                await send(pcm[j : j + chunk].ljust(chunk, b"\x00"))
            self._on_event("injection_speech_end", segment=i)  # ground-truth end of user speech
        self.all_injected.set()
        await silence_until(t0 + self._max_s)
        self._on_event("injection_max_reached")
