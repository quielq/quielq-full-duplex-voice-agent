"""Speaker guard: a live run must not play the agent into its own mic (no audio hardware used)."""

import pytest

from harness.audio_io import EchoRisk, check_echo_risk, is_builtin_speaker, local_transport
from harness.config import load_config


@pytest.mark.parametrize("name", ["MacBook Air Speakers", "MacBook Pro Speakers"])
def test_builtin_speakers_are_refused(name):
    assert is_builtin_speaker(name)
    with pytest.raises(EchoRisk, match="headphones"):
        check_echo_risk(name, allow_speakers=False)


def test_allow_speakers_overrides_the_refusal():
    check_echo_risk("MacBook Air Speakers", allow_speakers=True)


@pytest.mark.parametrize(
    "name", ["External Headphones", "AirPods Pro", "Jabra Speak2 40", "echo-cancel-sink"]
)
def test_headphones_and_speakerphones_pass(name):
    assert not is_builtin_speaker(name)
    check_echo_risk(name, allow_speakers=False)


@pytest.mark.parametrize("profile", ["mac", "pi"])
def test_profiles_keep_the_guard_on(profile):
    audio = load_config(profile).profile["audio"]
    assert audio["allow_speakers"] is False and audio["echo_cancel"] is False


def _transport_params(monkeypatch, **audio):
    monkeypatch.setattr("harness.audio_io.output_device_name", lambda _: "MacBook Air Speakers")
    monkeypatch.setattr("harness.audio_io.LocalAudioTransport", lambda params: params)
    base = {"in_sample_rate": 16000, "out_sample_rate": 24000, "echo_delay_ms": 50}
    return local_transport(base | audio)


def test_speakers_refused_without_echo_cancel(monkeypatch):
    with pytest.raises(EchoRisk):
        _transport_params(monkeypatch, allow_speakers=False, echo_cancel=False)


def test_echo_cancel_permits_speakers_and_sets_both_filters(monkeypatch):
    params = _transport_params(monkeypatch, allow_speakers=False, echo_cancel=True)
    assert params.audio_in_filter is not None and params.audio_out_filter is not None
