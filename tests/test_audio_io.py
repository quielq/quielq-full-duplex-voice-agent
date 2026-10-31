"""Speaker guard: a live run must not play the agent into its own mic (no audio hardware used)."""

import pytest

from harness.audio_io import EchoRisk, check_echo_risk, is_builtin_speaker
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
    assert load_config(profile).profile["audio"]["allow_speakers"] is False
