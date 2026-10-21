"""Step 5 regression: duck-then-decide (fakes plus real VAD and Smart Turn, no spend)."""

import shutil
import tempfile
from pathlib import Path

import pytest

from bench.interruption_test import run
from harness.config import load_config

pytestmark = pytest.mark.skipif(not shutil.which("say"), reason="macOS `say` not available")


def test_allowlist_loads_as_strings():
    # A bare `no` in YAML is a boolean; the profile must quote it.
    for profile in ("mac", "pi"):
        allow = load_config(profile).profile["barge_in"]["allowlist"]
        assert all(isinstance(w, str) for w in allow) and "no" in allow


async def test_backchannel_ducks_then_resumes():
    with tempfile.TemporaryDirectory() as d:
        r = await run("fake_words", 2.5, Path(d), barge_text="Mm-hmm.", barge_in="duck")
    assert r["duck_after_vad_ms"] is not None and r["duck_after_vad_ms"] < 100, r
    assert r["decision"] == "unduck:backchannel", r
    assert r["bot_kept_talking"], r
    assert r["committed_words"] == r["full_words"], r


async def test_allowlist_interrupts_and_keeps_history_in_order():
    with tempfile.TemporaryDirectory() as d:
        r = await run("fake_words", 2.5, Path(d), barge_text="Stop right there.", barge_in="duck")
    assert r["duck_after_vad_ms"] is not None and r["duck_after_vad_ms"] < 100, r
    assert r["decision"] == "barge_in_interrupt:allowlist", r
    # The assistant's partial reply directly follows the story request (helper returns ""
    # if the user's "stop" got in between), and is truncated to what played.
    assert 0 <= r["committed_words"] - r["heard_words"] <= 2, r
