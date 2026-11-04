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

from bench.interruption_test import fully_heard_words, run, timestamp_heard_words

pytestmark = pytest.mark.skipif(not shutil.which("say"), reason="macOS `say` not available")


async def test_word_timestamps_truncate_to_heard():
    with tempfile.TemporaryDirectory() as d:
        r = await run("fake_words", 2.5, Path(d))
    assert r["played_s"] > 2.0
    assert 0 <= r["overcommit_words"] <= 2, r
    assert r["committed_words"] < r["full_words"]
    assert r["stop_ms"] is not None and r["stop_ms"] < 500


async def test_fast_voice_is_measured_by_its_timestamps():
    """Regression (Nov 4): a voice faster than the 850 characters a minute guess, like
    Cartesia, looked 4 to 5 words over-committed because heard words came from the pace.
    The fast fake speaks 45 ms a character, so the guess says about half the words played."""
    with tempfile.TemporaryDirectory() as d:
        r = await run("fake_words_fast", 2.5, Path(d))
    assert r["heard_source"] == "word_timestamps", r
    assert 0 <= r["overcommit_words"] <= 2, r
    assert r["committed_words"] - r["heard_words_pace"] >= 3, r  # what the old estimate reported


async def test_without_word_timestamps_partial_sentence_is_not_committed():
    with tempfile.TemporaryDirectory() as d:
        r = await run("fake", 2.5, Path(d))
    assert r["committed_words"] == 0, r


def test_heard_words_counts_only_finished_words():
    text = "alone on a small rock"
    assert fully_heard_words(text, 3) == 0  # cut inside the first word
    assert fully_heard_words(text, 5) == 1  # ends exactly here
    assert fully_heard_words(text, 8) == 2  # the space after "on"
    assert fully_heard_words(text, 9) == 2  # the first letter of "a" is playing, "on" is finished
    assert fully_heard_words(text, 21) == 5


def test_timestamp_heard_words_needs_the_next_word_to_start():
    starts = [0.0, 0.3, 0.7, 1.0]
    assert timestamp_heard_words(starts, 0.0) == 0
    assert timestamp_heard_words(starts, 0.3) == 1  # the second word starts here, the first is done
    assert timestamp_heard_words(starts, 0.69) == 1
    assert timestamp_heard_words(starts, 5.0) == 3  # the last word's end is unknown
    assert timestamp_heard_words([], 5.0) == 0
