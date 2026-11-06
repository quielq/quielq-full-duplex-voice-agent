"""ADR-047: the LLM context keeps the last N user turns, never splitting a tool call. No spend."""

from pipecat.frames.frames import LLMContextFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.tests.utils import SleepFrame, run_test

from harness.config import load_config
from harness.context_window import ContextWindow, trim


def _turn(i, tool=False):
    msgs = [{"role": "user", "content": f"q{i}"}]
    if tool:
        msgs += [
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": f"c{i}", "type": "function", "function": {"name": "get_time", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": f"c{i}", "content": "three o'clock"},
        ]  # fmt: skip
    return msgs + [{"role": "assistant", "content": f"a{i}"}]


def _history(n, tool_every=2):
    return [m for i in range(n) for m in _turn(i, tool=i % tool_every == 0)]


def test_short_history_is_untouched():
    msgs = _history(5)
    assert trim(msgs, 20) == (msgs, 0)


def test_keeps_the_last_turns_and_starts_on_a_user_message():
    msgs = _history(30)
    kept, dropped = trim(msgs, 20)
    users = [m["content"] for m in kept if m["role"] == "user"]
    assert users == [f"q{i}" for i in range(10, 30)]
    assert kept[0] == {"role": "user", "content": "q10"}
    assert kept == msgs[dropped:]


def test_never_leaves_a_tool_result_without_its_call():
    kept, _ = trim(_history(25, tool_every=1), 3)
    call_ids = {c["id"] for m in kept for c in m.get("tool_calls") or []}
    assert all(m["tool_call_id"] in call_ids for m in kept if m["role"] == "tool")


def test_an_open_tool_call_after_the_last_user_message_is_kept():
    msgs = _history(25) + [{"role": "user", "content": "time?"}, _turn(99, tool=True)[1]]
    kept, _ = trim(msgs, 20)
    assert kept[-1]["tool_calls"][0]["id"] == "c99"


def test_zero_keeps_everything():
    msgs = _history(30)
    assert trim(msgs, 0) == (msgs, 0)


async def test_processor_trims_the_stored_context_and_logs_it():
    context = LLMContext(messages=_history(25))
    events = []
    proc = ContextWindow(20, on_event=lambda name, **f: events.append((name, f)))
    down, _ = await run_test(proc, frames_to_send=[LLMContextFrame(context), SleepFrame(0.02)],
                             expected_down_frames=None)  # fmt: skip
    assert any(isinstance(f, LLMContextFrame) for f in down)
    users = [m for m in context.get_messages() if m["role"] == "user"]
    assert len(users) == 20 and users[0]["content"] == "q5"
    assert events and events[0][0] == "context_trimmed" and events[0][1]["keep_turns"] == 20


def test_both_profiles_keep_twenty_turns():
    for profile in ("mac", "pi"):
        assert load_config(profile).profile["context"]["keep_turns"] == 20


def test_a_text_session_trims_and_logs_the_prompt_version(tmp_path):
    import asyncio
    import json

    from harness.bot import PROMPT_VERSION, SYSTEM_PROMPT, run_bot

    cfg = load_config("mac")
    cfg.profile["paths"] = {"logs_dir": str(tmp_path / "logs"), "data_dir": str(tmp_path / "data")}
    cfg.profile["context"]["keep_turns"] = 2
    turns = ["hello", "how are you", "tell me a joke", "thanks"]
    r = asyncio.run(run_bot(cfg, input_mode="text", turns=turns, data_dir=str(tmp_path / "data"),
                            reply_timeout_s=5, handle_sigint=False, servers={}))  # fmt: skip
    events = [json.loads(line) for line in open(r.events_path)]
    start = next(e for e in events if e["event"] == "run_start")
    assert start["prompt_version"] == PROMPT_VERSION and start["keep_turns"] == 2
    assert any(e["event"] == "context_trimmed" for e in events)
    users = [m["content"] for m in r.context_messages if m.get("role") == "user"]
    assert len(users) == 2 and users[-1] == "thanks"
    assert SYSTEM_PROMPT.startswith("You are Via")
