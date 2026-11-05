"""Taps on the face reach the model before the next user turn (ADR-043), on fakes."""

from pipecat.frames.frames import LLMMessagesAppendFrame, TranscriptionFrame
from pipecat.tests.utils import run_test

from harness.screen_notes import MAX_NOTES, ScreenNotes, screen_note

NEXT = '{"playing":true,"now_playing":"I Don\'t Love You by My Chemical Romance","volume":68}'


def heard(text="What song is playing right now?"):
    return TranscriptionFrame(text, "user", "2026-11-05T20:00:00")


def test_a_tap_that_ran_makes_a_note():
    note = screen_note("next_track", {}, NEXT)
    assert note.startswith("next_track() returned") and "I Don't Love You" in note
    assert "is_on" in screen_note("set_light", {"room": "bedroom", "is_on": True}, {"room": "bedroom", "is_on": True})


def test_a_tap_that_changed_nothing_makes_no_note():
    assert screen_note("resume_music", {}, '{"error":"Nothing has been played yet."}') is None
    assert screen_note("set_light", {"room": "x"}, {"status": "denied", "reason": "not available from the screen"}) is None
    assert screen_note("next_track", {}, "not json") is None


async def test_the_note_goes_in_before_the_next_transcript_and_does_not_run_the_model():
    notes = ScreenNotes()
    assert notes.add("next_track", {}, NEXT)
    down, _ = await run_test(notes, frames_to_send=[heard(), heard("and the volume?")])
    kinds = [type(f) for f in down]
    assert kinds == [LLMMessagesAppendFrame, TranscriptionFrame, TranscriptionFrame]  # only once
    append = down[0]
    assert append.run_llm is False
    assert append.messages[0]["role"] == "user" and "I Don't Love You" in append.messages[0]["content"]


async def test_no_tap_no_extra_frame():
    down, _ = await run_test(ScreenNotes(), frames_to_send=[heard()])
    assert [type(f) for f in down] == [TranscriptionFrame]


def test_a_run_of_taps_keeps_the_latest():
    notes = ScreenNotes()
    for step in range(MAX_NOTES + 3):
        notes.add("set_thermostat", {"target_c": 16 + step / 2}, {"target_c": 16 + step / 2})
    text = notes.take()[0]["content"]
    assert text.count("set_thermostat(") == MAX_NOTES and '"target_c":17.5' in text and '"target_c":16.0' not in text
    assert notes.take() == []
