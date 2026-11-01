"""Reply templates in place of the second LLM call (ADR-020). All fakes, no spend."""

import json
from types import SimpleNamespace

import pytest

from harness.bot import run_bot
from harness.config import load_config
from harness.tool_policy import ToolPolicy, ToolRule, spoken_duration


def _policy(enabled=True):
    cfg = load_config("mac").tool_policy
    cfg["reply_templates"]["enabled"] = enabled
    policy = ToolPolicy(cfg)
    policy.spoken = []

    async def speak(text, *, append_to_context=True):
        policy.spoken.append(text)

    policy.speak = speak
    return policy


def _params(user_text, args):
    calls = []

    async def result_callback(result, *, properties=None):
        calls.append((result, properties))

    context = SimpleNamespace(get_messages=lambda: [{"role": "user", "content": user_text}])
    return SimpleNamespace(arguments=args, context=context, result_callback=result_callback), calls


RULE = ToolRule("set_timer", "auto", 0.0, 10.0)


@pytest.mark.parametrize(
    "seconds,text",
    [(300, "5 minutes"), (1, "1 second"), (90, "1 minute 30 seconds"), (3600, "1 hour")],
)
def test_spoken_duration(seconds, text):
    assert spoken_duration(seconds) == text


async def test_success_is_spoken_and_skips_the_llm():
    policy = _policy()
    params, calls = _params("set a timer for five minutes", {"duration_seconds": 300})
    await policy._answer(RULE, params, json.dumps({"id": "a"}))
    assert policy.spoken == ["Timer set for 5 minutes."]
    assert calls[0][1].run_llm is False


@pytest.mark.parametrize(
    "text",
    [
        "turn off the light and set a timer for ten minutes",
        "set a timer then play jazz",
        "set a timer for two minutes. Also stop the music.",
        "set a timer for two minutes, stop the music",
        "turn off all the lights",
        "turn off both lamps",
    ],
)
async def test_a_request_that_may_need_another_tool_keeps_the_llm(text):
    policy = _policy()
    params, calls = _params(text, {"duration_seconds": 300})
    await policy._answer(RULE, params, "{}")
    assert policy.spoken == []
    assert calls[0][1] is None


@pytest.mark.parametrize("result", [{"error": "no light"}, '{"error": "no light"}'])
async def test_a_failure_keeps_the_llm(result):
    policy = _policy()
    params, calls = _params("set a timer", {"duration_seconds": 300})
    await policy._answer(RULE, params, result)
    assert policy.spoken == []
    assert calls[0][1] is None


async def test_missing_field_keeps_the_llm():
    policy = _policy()
    params, calls = _params("set a timer", {})
    await policy._answer(RULE, params, "{}")
    assert policy.spoken == []
    assert calls[0][1] is None


async def test_tool_without_a_template_keeps_the_llm():
    policy = _policy()
    params, calls = _params("list my timers", {})
    await policy._answer(ToolRule("list_timers", "auto", 0.0, 10.0), params, "{}")
    assert calls[0][1] is None


async def test_off_by_default():
    assert load_config("mac").tool_policy["reply_templates"]["enabled"] is False
    policy = _policy(enabled=False)
    params, calls = _params("set a timer", {"duration_seconds": 300})
    await policy._answer(RULE, params, "{}")
    assert policy.spoken == []
    assert calls[0][1] is None


async def _turns(tmp_path, turns):
    cfg = load_config("mac")
    cfg.providers["stages"] = {"stt": "fake", "llm": "fake", "tts": "fake"}
    cfg.tool_policy["reply_templates"]["enabled"] = True
    return await run_bot(cfg, input_mode="text", turns=turns, data_dir=str(tmp_path),
                         handle_sigint=False, reply_timeout_s=15)  # fmt: skip


async def test_through_the_bot_once_only_and_in_context(tmp_path):
    r = await _turns(tmp_path, ["set a timer for one minute"])
    assert [c["name"] for c in r.tool_calls] == ["set_timer"]
    assert "".join(r.spoken_text).count("Timer set for 1 minute.") == 1
    assert not r.llm_text  # no second LLM reply
    roles = [m.get("role") for m in r.context_messages]
    assert roles[-2:] == ["tool", "assistant"]
    assert "Timer set for 1 minute." in r.context_messages[-1]["content"]
    assert [d["decision"] for d in r.policy_decisions if d["tool"] == "set_timer"][-1] == "template_reply"


async def test_next_turn_follows_a_templated_reply(tmp_path):
    r = await _turns(tmp_path, ["set a timer for one minute", "what timers do I have"])
    assert [c["name"] for c in r.tool_calls] == ["set_timer", "list_timers"]
    assert [m["role"] for m in r.context_messages].count("user") == 2
