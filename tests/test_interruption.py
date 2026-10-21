"""Step 4 regression, free arms only: after a barge-in, what reaches the LLM context?

* fake_words (word timestamps, like Cartesia): the context holds the words that started
  playing, within a word or two of what was fully heard.
* fake (no word timestamps): Pipecat 1.13 commits a sentence only after it finishes
  playing, so a barge-in inside the first sentence commits nothing.

Aura-2 over-commits the whole reply; that arm spends money and runs from
bench/interruption_test.py, not here.
"""

import shutil
import tempfile
from pathlib import Path

import pytest

from bench.interruption_test import run

pytestmark = pytest.mark.skipif(not shutil.which("say"), reason="macOS `say` not available")


async def test_word_timestamps_truncate_to_heard():
    with tempfile.TemporaryDirectory() as d:
        r = await run("fake_words", 2.5, Path(d))
    assert r["played_s"] > 2.0
    assert 0 <= r["overcommit_words"] <= 2, r
    assert r["committed_words"] < r["full_words"]
    assert r["stop_ms"] is not None and r["stop_ms"] < 500


async def test_without_word_timestamps_partial_sentence_is_not_committed():
    with tempfile.TemporaryDirectory() as d:
        r = await run("fake", 2.5, Path(d))
    assert r["committed_words"] == 0, r
