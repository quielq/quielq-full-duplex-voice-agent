"""ADR-041: a waiting phrase the model writes before a tool call is dropped. No spend."""

from pipecat.frames.frames import (
    FunctionCallFromLLM,
    FunctionCallsStartedFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.tests.utils import SleepFrame, run_test

from harness.preamble import PreambleFilter


def _call():
    fc = FunctionCallFromLLM(function_name="local_search", tool_call_id="c", arguments={}, context=None)
    return FunctionCallsStartedFrame(function_calls=[fc])


def _reply(*tokens):
    return [LLMFullResponseStartFrame(), *[LLMTextFrame(t) for t in tokens]]


async def _spoken(frames):
    events = []
    proc = PreambleFilter(on_event=lambda name, **f: events.append((name, f)))
    down, _ = await run_test(proc, frames_to_send=frames + [SleepFrame(0.02)], expected_down_frames=None)
    return "".join(f.text for f in down if isinstance(f, LLMTextFrame)), events


async def test_preamble_before_a_tool_call_is_dropped():
    text, events = await _spoken([*_reply("I'll", " check your", " documents."), _call(), LLMFullResponseEndFrame()])
    assert text == ""
    assert events == [("preamble_dropped", {"text": "I'll check your documents."})]


async def test_a_call_that_overtakes_the_text_still_drops_it():
    # FunctionCallsStartedFrame is a system frame and can arrive before queued text.
    text, _ = await _spoken([LLMFullResponseStartFrame(), _call(), LLMTextFrame("Let me look that up."),
                             LLMFullResponseEndFrame()])  # fmt: skip
    assert text == ""


async def test_a_waiting_phrase_with_no_tool_call_is_kept():
    text, events = await _spoken([*_reply("Let me", " think."), LLMFullResponseEndFrame()])
    assert text == "Let me think." and events == []
    text, _ = await _spoken([*_reply("One moment.", " It is three o'clock."), LLMFullResponseEndFrame()])
    assert text == "One moment. It is three o'clock."


async def test_ordinary_replies_pass_at_once():
    events = []
    proc = PreambleFilter(on_event=lambda name, **f: events.append(name))
    frames = [*_reply("It is", " sunny"), SleepFrame(0.02)]
    down, _ = await run_test(proc, frames_to_send=frames, expected_down_frames=None)
    # Both tokens are out before the response ends: nothing was held.
    assert [f.text for f in down if isinstance(f, LLMTextFrame)] == ["It is", " sunny"]
    text, _ = await _spoken([*_reply("I", "'m sorry,", " I can't."), _call(), LLMFullResponseEndFrame()])
    assert text == "I'm sorry, I can't."


async def test_text_after_the_tool_result_is_spoken():
    frames = [*_reply("Let me check."), _call(), LLMFullResponseEndFrame(),
              *_reply("Mayon Builders is managed by Ana Cruz."), LLMFullResponseEndFrame()]  # fmt: skip
    text, _ = await _spoken(frames)
    assert text == "Mayon Builders is managed by Ana Cruz."


async def test_interruption_clears_held_text():
    text, _ = await _spoken([*_reply("Let me"), InterruptionFrame(), *_reply("Sure."), LLMFullResponseEndFrame()])
    assert text == "Sure."
