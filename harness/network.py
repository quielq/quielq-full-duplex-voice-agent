"""Network-loss detection (ADR-037): say once that the connection is gone, then wait for it.

A cloud stage that fails pushes an ErrorFrame: a refused WebSocket connect in the Flux or
Cartesia service (and the ``voice_listen`` and ``voice_speak`` relays, whose connect failures
surface the same way), an LLM request that cannot reach its host, or a TTS error. One error
does not mean the network is down, so ``NetworkWatch`` then tries a plain TCP connect to that
stage's provider host with a short timeout. That opens no TLS session and makes no API call,
so it is free. If the host answers, the error was one bad request and the normal recovery
(``harness/recovery.py``) handles it. If it does not, that is an outage:

* the local ``network_lost`` clip plays once, through ``ClipPlayer``, with no TTS call;
* every other output (the waiting phrase, a late reply, the LLM error phrase) is held back;
* the mic, VAD, and turn detection keep running, since they are all local;
* a request the LLM could not answer is taken out of the context, because the user was told
  it was lost and should not find it acted on minutes later;
* the host is probed again with backoff until it answers, then ``network_restored`` is logged
  with how long the outage lasted, and the next turn runs as usual.

The clip is raw output audio with no text, so it never reaches the LLM context, and an
interruption flushes it like any other queued output audio. The face shows the outage from
the ``network_lost`` and ``network_restored`` events (``harness/ui.py``).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable, Sequence

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    ErrorFrame,
    Frame,
    InterruptionFrame,
    OutputAudioRawFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.llm_service import LLMService
from pipecat.services.stt_service import STTService
from pipecat.services.tts_service import TTSService

from harness.phrase_cache import load_phrase

# The host each stage's provider connects to. Relay stages probe the provider their voice
# server talks to, which runs on this machine and shares its network.
STAGE_HOSTS: dict[str, tuple[str, int]] = {
    "deepgram_flux": ("api.deepgram.com", 443),
    "voice_listen": ("api.deepgram.com", 443),
    "deepgram_aura2": ("api.deepgram.com", 443),
    "cartesia": ("api.cartesia.ai", 443),
    "voice_speak": ("api.cartesia.ai", 443),
    "anthropic": ("api.anthropic.com", 443),
    "groq": ("api.groq.com", 443),
    "openrouter": ("openrouter.ai", 443),
}

Probe = Callable[[str, int, float], Awaitable[bool]]


async def tcp_reachable(host: str, port: int, timeout_s: float) -> bool:
    """True if a TCP connection to host:port opens within the timeout. DNS counts as part of it."""
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout_s)
    except (OSError, TimeoutError):
        return False
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return True


def stage_hosts(stages: dict[str, str]) -> dict[str, tuple[str, int]]:
    """The provider host for each of stt, llm, and tts that uses one. Fakes have none."""
    return {s: STAGE_HOSTS[stages[s]] for s in ("stt", "llm", "tts") if stages.get(s) in STAGE_HOSTS}


def stage_of(source: object) -> str | None:
    if isinstance(source, STTService):
        return "stt"
    if isinstance(source, LLMService):
        return "llm"
    if isinstance(source, TTSService):
        return "tts"
    return None


class ClipPlayer(FrameProcessor):
    """Plays stored phrase clips as output audio, and holds other output back during an outage.

    It sits just before the output transport, after the waiting phrase. Its own clips are
    pushed from here, so they pass; anything arriving from upstream while ``muted`` is dropped.
    """

    def __init__(self, sample_rate: int, *, muted: Callable[[], bool] = lambda: False,
                 on_event: Callable[..., object] | None = None, **kwargs):  # fmt: skip
        super().__init__(**kwargs)
        self._rate = sample_rate
        self._muted = muted
        self._on_event = on_event or (lambda *a, **k: None)
        self._clips: dict[str, bytes] = {}
        self._awaiting_bot_start = False
        self._bot_speaking = False
        self.played: list[str] = []

    def _clip(self, name: str) -> bytes:
        if name not in self._clips:
            pcm, real = load_phrase(name, self._rate)
            if not real:
                logger.warning(f"no clip for {name}: run bench.make_phrases (paid), or hear a tone")
            self._on_event("phrase_clip", phrases=[name], real=int(real))
            self._clips[name] = pcm
        return self._clips[name]

    def claim_bot_start(self) -> bool:
        """True once, for the bot start a clip caused. Called by the event observer."""
        claimed, self._awaiting_bot_start = self._awaiting_bot_start, False
        return claimed

    async def play(self, name: str) -> None:
        pcm = self._clip(name)
        self.played.append(name)
        self._awaiting_bot_start = not self._bot_speaking
        self._on_event("phrase_played", phrase=name, ms=len(pcm) * 500 // self._rate)
        chunk = self._rate // 50 * 2  # 20 ms of 16-bit samples
        for i in range(0, len(pcm), chunk):
            await self.push_frame(OutputAudioRawFrame(pcm[i : i + chunk], self._rate, 1))

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, InterruptionFrame):
            self._awaiting_bot_start = False
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
        if direction is FrameDirection.DOWNSTREAM and isinstance(frame, OutputAudioRawFrame) and self._muted():
            return  # held back: the network phrase is the last thing said until it is back
        await self.push_frame(frame, direction)


class NetworkWatch(BaseObserver):
    """Tells a network outage from one bad request, and waits out the outage."""

    def __init__(
        self,
        hosts: dict[str, tuple[str, int]],
        *,
        play: Callable[[], Awaitable[None]] | None = None,
        on_turn_lost: Callable[[str], object] | None = None,
        on_event: Callable[..., object] | None = None,
        probe: Probe | None = None,
        timeout_s: float = 1.5,
        attempts: int = 2,
        backoff_s: Sequence[float] = (1, 2, 4, 8),
        **kwargs,
    ):
        """``play`` says the phrase. ``on_turn_lost(stage)`` ends a turn that will get no reply."""
        super().__init__(**kwargs)
        self._hosts = hosts
        self._play = play
        self._on_turn_lost = on_turn_lost or (lambda stage: None)
        self._on_event = on_event or (lambda *a, **k: None)
        self._probe = probe or tcp_reachable
        self._timeout_s = timeout_s
        self._attempts = max(1, attempts)
        self._backoff = list(backoff_s) or [1.0]
        self._checks: dict[str, asyncio.Task] = {}
        self._failed: dict[str, tuple[str, int]] = {}
        self._recovery: asyncio.Task | None = None
        self._down_since = 0.0
        self.down = False
        self.outages = 0
        self.on_restored: list[Callable[[], Awaitable[None]]] = []

    async def on_push_frame(self, data: FramePushed):
        if isinstance(data.frame, ErrorFrame) and data.first_push:
            stage = stage_of(data.source)
            if stage:
                await self.check(stage, data.frame.error)

    async def check(self, stage: str, error: str = "") -> bool:
        """True if this stage's failure is part of a network outage. Errors at once share a probe."""
        if self.down:
            if stage in self._hosts:
                self._failed.setdefault(stage, self._hosts[stage])
            self._on_turn_lost(stage)
            return True
        if stage not in self._hosts:
            return False
        task = self._checks.get(stage)
        if task is None:
            task = asyncio.create_task(self._classify(stage, str(error)))
            self._checks[stage] = task
            task.add_done_callback(lambda _: self._checks.pop(stage, None))
        return await asyncio.shield(task)

    async def _reachable(self, host: tuple[str, int]) -> bool:
        for i in range(self._attempts):
            if await self._probe(host[0], host[1], self._timeout_s):
                return True
            if i + 1 < self._attempts:
                await asyncio.sleep(0.2)
        return False

    async def _classify(self, stage: str, error: str) -> bool:
        host = self._hosts[stage]
        t0 = time.monotonic()
        if await self._reachable(host):
            self._on_event("provider_error", stage=stage, host=host[0], reachable=True, error=error[:200],
                           probe_ms=round((time.monotonic() - t0) * 1000))  # fmt: skip
            return False
        if self.down:  # another stage's probe got there first
            self._failed.setdefault(stage, host)
            return True
        self.down = True
        self.outages += 1
        self._down_since = time.monotonic()
        self._failed = {stage: host}
        self._on_event("network_lost", stage=stage, host=host[0], error=error[:200],
                       probe_ms=round((self._down_since - t0) * 1000))  # fmt: skip
        logger.warning(f"network lost: {stage} cannot reach {host[0]}")
        self._recovery = asyncio.create_task(self._wait_for_network())
        if self._play:
            await self._play()
        self._on_turn_lost(stage)
        return True

    async def _wait_for_network(self) -> None:
        probes = 0
        while True:
            await asyncio.sleep(self._backoff[min(probes, len(self._backoff) - 1)])
            probes += 1
            results = [await self._probe(h, p, self._timeout_s) for h, p in set(self._failed.values())]
            if all(results):
                break
        down_ms = round((time.monotonic() - self._down_since) * 1000)
        stages = sorted(self._failed)
        self.down = False
        self._failed = {}
        self._on_event("network_restored", stages=stages, down_ms=down_ms, probes=probes)
        logger.info(f"network back after {down_ms} ms ({', '.join(stages)})")
        for callback in self.on_restored:
            try:
                await callback()
            except Exception as e:  # a hook must not stop the watch
                logger.error(f"network restore hook failed: {e}")

    async def close(self) -> None:
        for task in [self._recovery, *self._checks.values()]:
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
