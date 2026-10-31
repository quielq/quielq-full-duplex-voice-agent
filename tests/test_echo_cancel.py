"""EchoCanceller with synthetic audio, in the transport's rates and chunk sizes (no hardware)."""

import numpy as np

from harness.ducking import DuckGain
from harness.echo_cancel import MAX_REFERENCE_S, EchoCanceller

RATE = 16000


def _noise(seconds: float, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(int(seconds * RATE)) * 3000).astype(np.int16)


def _rms(audio: bytes) -> float:
    return float(np.sqrt(np.mean(np.frombuffer(audio, dtype=np.int16).astype(np.float64) ** 2)))


def _run(canceller: EchoCanceller, far: np.ndarray, near: np.ndarray) -> bytes:
    """40 ms reference bursts, 20 ms mic blocks, as the Pipecat transport delivers them."""
    far_b, near_b = far.tobytes(), near.tobytes()
    out, burst, block = [], RATE * 2 // 25, RATE * 2 // 50
    for start in range(0, len(near_b), block):
        if start % burst == 0:
            canceller.add_reference(far_b[start : start + burst])
        out.append(canceller.process(near_b[start : start + block]))
    return b"".join(out)


def _echo(far: np.ndarray, lag_ms: int = 60) -> np.ndarray:
    lag = lag_ms * RATE // 1000
    return (np.concatenate([np.zeros(lag), far[:-lag]]) * 0.5).astype(np.int16)


def test_bursty_reference_still_cancels():
    far = _noise(10)
    out = _run(EchoCanceller(RATE, RATE, 50), far, _echo(far))
    tail = len(out) // 2
    assert _rms(out[tail:]) < _rms(_echo(far).tobytes()[tail:]) / 3


def test_near_speech_survives_with_no_reference():
    near = _noise(4, seed=1)
    out = _run(EchoCanceller(RATE, RATE, 50), np.zeros_like(near), near)
    assert _rms(out) > _rms(near.tobytes()) / 2


def test_output_rate_reference_is_resampled_to_10_ms_frames():
    canceller = EchoCanceller(16000, 24000, 50)
    for _ in range(10):
        canceller.add_reference(bytes(24000 * 2 // 25))  # 40 ms at 24 kHz
    assert 30 <= len(canceller._reference) <= 40
    assert all(len(frame) == 320 for frame in canceller._reference)


def test_reference_queue_is_bounded():
    canceller = EchoCanceller(RATE, RATE, 50)
    canceller.add_reference(bytes(RATE * 2 * (MAX_REFERENCE_S + 2)))
    assert len(canceller._reference) == MAX_REFERENCE_S * 100


def test_mic_audio_comes_back_in_whole_frames():
    canceller = EchoCanceller(RATE, RATE, 50)
    assert canceller.process(bytes(100)) == b""
    assert len(canceller.process(bytes(540))) == 640


async def test_reference_includes_the_ducking_gain():
    canceller = EchoCanceller(RATE, RATE, 50)
    duck = DuckGain()
    duck.set_gain(0.5)
    tap = canceller.reference_filter(duck)
    audio = np.full(160, 1000, dtype=np.int16).tobytes()
    played = await tap.filter(audio)
    assert np.frombuffer(played, dtype=np.int16)[0] == 500
    assert canceller._reference[0] == played


def test_reports_levels_and_underruns_once_a_second():
    events = []
    canceller = EchoCanceller(
        RATE, RATE, 50, on_event=lambda name, **fields: events.append((name, fields))
    )
    canceller.process(_noise(0.5).tobytes())
    assert events == []
    canceller.process(_noise(0.5).tobytes())
    name, fields = events[0]
    assert name == "echo_window"
    assert fields["underruns"] == 100 and fields["far_db"] < -90 < fields["near_db"]


def test_near_level_is_measured_before_cancelling():
    events = []
    canceller = EchoCanceller(
        RATE, RATE, 50, on_event=lambda name, **fields: events.append(fields)
    )
    far = _noise(4)
    _run(canceller, far, _echo(far))
    assert events[-1]["out_db"] < events[-1]["near_db"] - 6


def test_output_gap_is_reported_but_a_quiet_bot_is_not():
    events = []
    canceller = EchoCanceller(
        RATE, RATE, 50, on_event=lambda name, **fields: events.append(fields)
    )
    canceller.add_reference(bytes(320))
    canceller._last_reference -= 1.2
    canceller.add_reference(bytes(320))
    canceller._last_reference -= 60
    canceller.add_reference(bytes(320))
    canceller.process(bytes(32000))
    assert events[0]["max_output_gap_ms"] == 1200
