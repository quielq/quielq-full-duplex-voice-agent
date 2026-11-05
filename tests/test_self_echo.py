"""ADR-042: the bot's own words heard back through the speakers are not answered. No spend."""

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    TranscriptionFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
)
from pipecat.tests.utils import SleepFrame, run_test

from harness.config import load_config
from harness.self_echo import SelfEchoFilter, SpokenTap, SpokenWords, is_run_of, words

SAID = "I can't delete files for good, but I can move them aside first."


def _final(text):
    return TranscriptionFrame(text, "user", "t")


async def _passed(spoken, frames, **kwargs):
    events = []
    proc = SelfEchoFilter(spoken, on_event=lambda name, **f: events.append(f["text"]), **kwargs)
    down, _ = await run_test(proc, frames_to_send=frames + [SleepFrame(0.01)], expected_down_frames=None)
    return [f.text for f in down if isinstance(f, TranscriptionFrame)], events


def _spoken(text=SAID, **kwargs):
    s = SpokenWords(**kwargs)
    s.add(text)
    return s


async def test_own_words_that_cut_the_bot_off_are_dropped():
    passed, events = await _passed(_spoken(), [BotStartedSpeakingFrame(), UserStartedSpeakingFrame(),
                                               BotStoppedSpeakingFrame(), _final("I can't delete.")])  # fmt: skip
    assert passed == [] and events == ["I can't delete."]


async def test_the_same_words_after_the_bot_finished_are_kept():
    # A person repeating an option back must be heard.
    frames = [BotStartedSpeakingFrame(), BotStoppedSpeakingFrame(), UserStartedSpeakingFrame(),
              _final("I can't delete")]  # fmt: skip
    assert (await _passed(_spoken(), frames))[0] == ["I can't delete"]


async def test_new_words_allowlist_words_and_short_fragments_are_kept():
    cut = [BotStartedSpeakingFrame(), UserStartedSpeakingFrame()]
    for text in ["Delete the delegate trial folder.", "No, I can't delete", "move them aside",
                 "for good"]:  # fmt: skip
        expected = [] if text == "move them aside" else [text]
        assert (await _passed(_spoken(), [*cut, _final(text)]))[0] == expected, text


def test_matching_is_in_order_and_contiguous():
    spoken = words(SAID)
    assert is_run_of(words("I CAN'T delete"), spoken)
    assert not is_run_of(words("delete can't I"), spoken)
    assert not is_run_of(words("I can't move"), spoken)


def test_old_words_fall_out_of_the_window():
    now = [0.0]
    s = SpokenWords(window_s=15, clock=lambda: now[0])
    s.add(SAID)
    now[0] = 16.0
    assert s.recent() == []


async def test_the_tap_records_tts_text_and_passes_it():
    s = SpokenWords()
    down, _ = await run_test(SpokenTap(s), frames_to_send=[TTSTextFrame("Hello", text_type="word"),
                                                           TTSTextFrame("there", text_type="word")],
                             expected_down_frames=None)  # fmt: skip
    assert s.recent() == ["hello", "there"] and len([f for f in down if isinstance(f, TTSTextFrame)]) == 2


def test_profiles_have_it_with_echo_cancel():
    for profile in ("mac", "pi"):
        se = load_config(profile).profile["self_echo"]
        assert se["enabled"] and se["min_words"] >= 3


async def test_a_whole_run_does_not_answer_its_own_words(tmp_path):
    import json
    import shutil
    import subprocess

    import pytest

    from harness.bot import run_bot
    from harness.file_input import Segment

    if not shutil.which("say"):
        pytest.skip("macOS `say` not available")
    clips = {}
    for name, text in (("ask", "Tell me a story."), ("echo", "a lighthouse keeper named Ana")):
        clips[name] = tmp_path / f"{name}.wav"
        subprocess.run(["say", "-o", str(clips[name]), "--data-format=LEI16@16000", text], check=True)
    cfg = load_config("mac")
    cfg.providers["stages"] = {"stt": "fake", "llm": "fake", "tts": "fake_words"}
    cfg.profile["paths"] = {"logs_dir": str(tmp_path / "logs"), "data_dir": str(tmp_path / "data")}
    cfg.profile["audio"]["echo_cancel"] = True
    segments = [Segment(clips["ask"]), Segment(clips["echo"], after="bot_speaking", delay_s=2.5)]
    r = await run_bot(cfg, input_mode="wav", segments=segments, data_dir=str(tmp_path / "d"),
                      stt_script=["Tell me a story.", "a lighthouse keeper named Ana"],
                      handle_sigint=False, reply_timeout_s=30)  # fmt: skip
    events = [json.loads(line) for line in r.events_path.read_text().splitlines()]
    assert [e["text"] for e in events if e["event"] == "self_echo"] == ["a lighthouse keeper named Ana"]
    users = [m for m in r.context_messages if m.get("role") == "user"]
    assert len(users) == 1  # the echo never reached the LLM as the user's words
