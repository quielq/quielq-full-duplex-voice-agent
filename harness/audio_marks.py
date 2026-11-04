"""Audio marks for the latency benchmark (ADR-036): sample-level times, never Pipecat frames.

Two taps, both a threshold on the PCM itself:

* ``InputVoiceTap`` sits right after the mic (or the scripted wav source), after the echo
  canceller. It logs ``user_audio_onset`` and ``user_audio_end`` with ``audio_t_ns``, the
  monotonic time of the first or last sample over the threshold. A run ends after
  ``gap_ms`` below it, so a pause inside a sentence is not an end.
* ``OutputVoiceTap`` wraps the output transport's ``write_audio_frame``, the call that hands
  samples to the device (PyAudio, or the paced null output in wav runs). It logs
  ``reply_audio_onset`` for TTS audio and ``local_audio_onset`` for everything else, which is
  the thinking phrase (ADR-009, ADR-033). Only ``reply_audio_onset`` is first reply audio.

Pipecat cuts output audio into chunks that keep the class of the frame they came from, so a
``TTSAudioRawFrame`` chunk is reply audio and a plain ``OutputAudioRawFrame`` chunk is a local
clip. A chunk that straddles a clip and a reply is marked ``boundary``; the thinking phrase
pads its clips to whole chunks so that this does not happen in practice.

Play time models the device as a queue: a chunk starts when the one before it ends, or at the
write call plus the device's output latency after an underrun. Capture time is the time of the
frame's first sample, stamped in the PyAudio callback (``stamp_capture``) or by the scripted
source, less the device's input latency.
"""

from __future__ import annotations

import collections
import time
from collections.abc import Callable

import numpy as np
from pipecat.frames.frames import Frame, InputAudioRawFrame, TTSAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

INPUT_THRESHOLD = 500  # int16 peak, about -36 dBFS; speech_onset_s in file_input uses the same
OUTPUT_THRESHOLD = 64  # int16 peak, about -54 dBFS; TTS audio is clean (null_output uses 64)
GAP_MS = 300  # quieter than the threshold this long ends a run
NOISE_FACTOR = 4.0  # a live mic's threshold rises to this many times its noise floor


def _voiced(audio: bytes, threshold: float) -> np.ndarray:
    """Indices of the samples whose magnitude reaches ``threshold``."""
    samples = np.frombuffer(audio[: len(audio) // 2 * 2], dtype="<i2").astype(np.int32)
    return np.flatnonzero(np.abs(samples) >= threshold)


class InputVoiceTap(FrameProcessor):
    """Logs when the user's audio starts and stops, from the samples."""

    def __init__(
        self,
        on_event: Callable[..., object],
        *,
        threshold: int = INPUT_THRESHOLD,
        gap_ms: float = GAP_MS,
        adaptive: bool = False,
        clock_ns: Callable[[], int] = time.monotonic_ns,
        **kwargs,
    ):
        """``adaptive`` raises the threshold over a live mic's noise floor (never lowers it)."""
        super().__init__(**kwargs)
        self._on_event = on_event
        self._min_threshold = threshold
        self._gap_ns = int(gap_ms * 1e6)
        self._adaptive = adaptive
        self._clock_ns = clock_ns
        self._floor: float | None = None
        self._in_speech = False
        self._onset_ns = 0
        self._last_voiced_ns = 0

    @property
    def threshold(self) -> float:
        return max(self._min_threshold, (self._floor or 0.0) * NOISE_FACTOR)

    def observe(self, audio: bytes, sample_rate: int, start_ns: int) -> None:
        """Scan one frame whose first sample was captured at ``start_ns``."""
        n = len(audio) // 2
        if n == 0:
            return
        idx = _voiced(audio, self.threshold)
        if self._adaptive:
            self._track_floor(audio)
        ns_per_sample = 1e9 / sample_rate
        if idx.size:
            first_ns = start_ns + int(idx[0] * ns_per_sample)
            if not self._in_speech:
                self._in_speech = True
                self._onset_ns = first_ns
                self._on_event("user_audio_onset", audio_t_ns=first_ns, threshold=round(self.threshold))
            self._last_voiced_ns = start_ns + int(idx[-1] * ns_per_sample)
        end_ns = start_ns + int(n * ns_per_sample)
        if self._in_speech and end_ns - self._last_voiced_ns >= self._gap_ns:
            self._in_speech = False
            self._on_event("user_audio_end", audio_t_ns=self._last_voiced_ns,
                           speech_ms=round((self._last_voiced_ns - self._onset_ns) / 1e6),
                           threshold=round(self.threshold))  # fmt: skip

    def _track_floor(self, audio: bytes) -> None:
        """Peak of the quietest recent 10 ms: drops at once, rises over about ten seconds."""
        samples = np.abs(np.frombuffer(audio[: len(audio) // 2 * 2], dtype="<i2").astype(np.int32))
        block = max(1, samples.size // 2)
        for i in range(0, samples.size, block):
            peak = float(samples[i : i + block].max())
            if self._floor is None or peak < self._floor:
                self._floor = peak
            else:
                self._floor += (peak - self._floor) * 0.001

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame) and direction == FrameDirection.DOWNSTREAM:
            start_ns = getattr(frame, "capture_ns", None)
            if start_ns is None:  # a device delivers a frame once it has been captured
                start_ns = self._clock_ns() - int(len(frame.audio) / 2 / frame.sample_rate * 1e9)
            self.observe(frame.audio, frame.sample_rate, start_ns)
        await self.push_frame(frame, direction)


def stamp_capture(input_transport) -> None:
    """Stamp each mic frame with the time of its first sample, taken in the PyAudio callback.

    Call before the pipeline starts: the callback is handed to PyAudio when the stream opens.
    Each callback schedules one ``push_audio_frame`` in order, so a FIFO pairs them.
    """
    times: collections.deque[int] = collections.deque()
    callback = input_transport._audio_in_callback
    push = input_transport.push_audio_frame
    latency_ns: list[int] = []

    def stamped_callback(in_data, frame_count, time_info, status):
        times.append(time.monotonic_ns())
        return callback(in_data, frame_count, time_info, status)

    async def stamped_push(frame):
        if times:
            if not latency_ns:
                stream = getattr(input_transport, "_in_stream", None)
                latency_ns.append(int(stream.get_input_latency() * 1e9) if stream else 0)
            duration_ns = int(len(frame.audio) / 2 / frame.sample_rate * 1e9)
            frame.capture_ns = times.popleft() - duration_ns - latency_ns[0]
        await push(frame)

    input_transport._audio_in_callback = stamped_callback
    input_transport.push_audio_frame = stamped_push


class OutputVoiceTap:
    """Logs the play time of the first sample over the threshold in each run of output audio."""

    def __init__(
        self,
        on_event: Callable[..., object],
        *,
        threshold: int = OUTPUT_THRESHOLD,
        gap_ms: float = GAP_MS,
        device_latency_s: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        """``device_latency_s`` None reads the PyAudio stream's output latency at the first write."""
        self._on_event = on_event
        self._threshold = threshold
        self._gap_ns = int(gap_ms * 1e6)
        self._latency_s = device_latency_s
        self._clock = clock
        self._next_t = 0.0
        self._last_voiced_ns: dict[str, int | None] = {"reply": None, "local": None}
        self._last_kind: str | None = None

    def attach(self, output) -> None:
        write = output.write_audio_frame

        async def tapped_write(frame):
            if self._latency_s is None:
                stream = getattr(output, "_out_stream", None)
                self._latency_s = float(stream.get_output_latency()) if stream else 0.0
                self._on_event("audio_out_latency", ms=round(self._latency_s * 1000, 1))
            self.observe(frame)
            return await write(frame)

        output.write_audio_frame = tapped_write

    def observe(self, frame) -> None:
        kind = "reply" if isinstance(frame, TTSAudioRawFrame) else "local"
        duration = len(frame.audio) / 2 / frame.sample_rate
        start = max(self._clock() + (self._latency_s or 0.0), self._next_t)
        self._next_t = start + duration
        boundary = self._last_kind is not None and self._last_kind != kind
        self._last_kind = kind
        idx = _voiced(frame.audio, self._threshold)
        if not idx.size:
            return
        start_ns = round(start * 1e9)
        ns_per_sample = 1e9 / frame.sample_rate
        first_ns = start_ns + round(idx[0] * ns_per_sample)
        last = self._last_voiced_ns[kind]
        if last is None or first_ns - last >= self._gap_ns:
            self._on_event(f"{kind}_audio_onset", audio_t_ns=first_ns, boundary=boundary)
        self._last_voiced_ns[kind] = start_ns + round(idx[-1] * ns_per_sample)
