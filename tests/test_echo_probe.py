"""Echo probe maths on synthetic audio (no hardware)."""

import numpy as np
from bench.echo_probe import IN_RATE, OUT_RATE, cancel, measure_lag_ms, mix_talker, reference, rms_db


def _noise(seconds: float) -> np.ndarray:
    rng = np.random.default_rng(0)
    return (rng.standard_normal(int(seconds * IN_RATE)) * 3000).astype(np.int16)


def _echo(far: np.ndarray, lag_ms: int) -> bytes:
    lag = lag_ms * IN_RATE // 1000
    return (np.concatenate([np.zeros(lag), far[:-lag]]) * 0.5).astype(np.int16).tobytes()


def test_measure_lag_finds_the_delay():
    far = _noise(3)
    assert abs(measure_lag_ms(far.tobytes(), _echo(far, 120)) - 120) <= 1


def test_reference_is_padded_to_capture_length():
    far = np.zeros(24000, dtype=np.int16).tobytes()
    assert len(reference(far, 64000)) == 64000


def test_aec_reduces_a_pure_echo():
    far = _noise(10)
    near = _echo(far, 60)
    cancelled = cancel(near, far.tobytes(), 60)
    tail = len(cancelled) // 2
    assert rms_db(cancelled[tail:]) < rms_db(near[tail:]) - 10


def test_talker_goes_to_the_speakers_at_its_window():
    far = np.zeros(OUT_RATE * 20, dtype=np.int16).tobytes()
    mixed, windows = mix_talker(far, np.full(OUT_RATE, 1000, dtype=np.int16).tobytes())
    samples = np.frombuffer(mixed, dtype=np.int16)
    assert samples[OUT_RATE * 8] == 1000 and samples[OUT_RATE * 7] == 0
    assert windows[0] == (8.5, 9.5)
