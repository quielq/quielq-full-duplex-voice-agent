"""Echo probe maths on synthetic audio (no hardware). AEC needs `uv run --with livekit`."""

import numpy as np
import pytest

from bench.echo_probe import IN_RATE, cancel, measure_lag_ms, reference, rms_db


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
    pytest.importorskip("livekit")
    far = _noise(10)
    near = _echo(far, 60)
    cancelled = cancel(near, far.tobytes(), 60)
    tail = len(cancelled) // 2
    assert rms_db(cancelled[tail:]) < rms_db(near[tail:]) - 10
