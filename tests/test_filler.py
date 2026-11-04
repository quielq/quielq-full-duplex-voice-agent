"""ADR-009 waiting phrase, on real Silero VAD with fake STT, LLM, and TTS. No spend."""

import asyncio
import json
import shutil
import subprocess

import pytest

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    FunctionCallInProgressFrame,
    InterruptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.tests.utils import SleepFrame, run_test

from bench.latency_probe import filler_rate, stage_deltas
from harness import bot
from harness.bot import run_bot
from harness.config import load_config
from harness.fakes import RuleBasedLLMService
from harness.filler import FillerProcessor
from harness.phrase_cache import PHRASE_DIR, PHRASES, load_phrase

pytestmark = pytest.mark.skipif(not shutil.which("say"), reason="macOS `say` not available")


@pytest.fixture
def cfg(tmp_path):
    cfg = load_config("mac")
    cfg.providers["stages"] = {"stt": "fake", "llm": "fake", "tts": "fake"}
    cfg.profile["paths"] = {"logs_dir": str(tmp_path / "logs"), "data_dir": str(tmp_path / "data")}
    cfg.profile["filler"]["enabled"] = True
    cfg.profile["filler"]["slow_tools"] = ["set_timer"]  # the fake LLM's one tool
    return cfg


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    path = tmp_path_factory.mktemp("audio") / "request.wav"
    subprocess.run(["say", "-o", str(path), "--data-format=LEI16@16000", "Set a timer for one minute."], check=True)
    return path


class SlowLLM(RuleBasedLLMService):
    async def _respond(self, context):
        await asyncio.sleep(2.0)  # past after_ms (1500)
        await super()._respond(context)


async def _run(cfg, tmp_path, clip, text):
    r = await run_bot(cfg, input_mode="wav", wav=clip, say=text, data_dir=str(tmp_path / "d"),
                      handle_sigint=False, reply_timeout_s=30)  # fmt: skip
    return r, [json.loads(line) for line in r.events_path.read_text().splitlines()]


def named(events, name):
    return [e for e in events if e["event"] == name]


def test_on_in_both_profiles_for_slow_tools_only():
    for profile in ("mac", "pi"):
        f = load_config(profile).profile["filler"]
        assert f["enabled"] is True and f["after_ms"] == 1500
        assert len(f["phrases"]) >= 4 and set(f["phrases"]) <= set(PHRASES)
        assert "search" in f["slow_tools"] and "set_timer" not in f["slow_tools"]


def test_the_prompt_leaves_waiting_to_the_device():
    assert "Never open a reply with a waiting phrase" in bot.SYSTEM_PROMPT


def test_missing_clip_is_a_placeholder(tmp_path):
    pcm, real = load_phrase("one_sec", 24000, tmp_path)
    assert pcm and real is False
    assert set(PHRASES) >= {"one_sec", "network_lost", "cap_reached"}
    with pytest.raises(KeyError):
        load_phrase("nope", 24000)


async def test_plays_once_on_a_tool_call_and_stays_out_of_context(cfg, tmp_path, clip):
    r, ev = await _run(cfg, tmp_path, clip, "set a timer for one minute")
    assert [e["reason"] for e in named(ev, "filler_trigger")] == ["tool_call"]
    assert len(named(ev, "filler_audio_out")) == 1
    audio = named(ev, "first_audio_out")
    assert len(audio) == 1 and audio[0]["t_ns"] > named(ev, "filler_audio_out")[0]["t_ns"]
    assert "Timer set" in r.spoken_text
    text = json.dumps(r.context_messages).lower()
    assert "one sec" not in text
    clip_event = named(ev, "filler_clip")[0]
    real = sum((PHRASE_DIR / f"{n}.wav").exists() for n in clip_event["phrases"])
    assert clip_event["real"] == real  # the clips are committed, so this is all five
    assert filler_rate(ev) == (1, 1)
    deltas = next(iter(stage_deltas(ev).values()))
    assert "end_of_turn_to_filler" in deltas
    assert deltas["end_of_turn_to_filler"] < deltas["speech_end_to_audio_out"]


async def test_timer_fires_when_the_reply_is_slow(cfg, tmp_path, clip, monkeypatch):
    monkeypatch.setattr(bot, "build_llm", lambda *a, **k: SlowLLM())
    _, ev = await _run(cfg, tmp_path, clip, "hello there")
    assert [e["reason"] for e in named(ev, "filler_trigger")] == ["timer"]


async def test_skipped_when_the_reply_is_quick(cfg, tmp_path, clip):
    _, ev = await _run(cfg, tmp_path, clip, "hello there")
    assert named(ev, "filler_trigger") == []
    assert len(named(ev, "first_audio_out")) == 1




async def _triggers(frames, **kwargs) -> list:
    fired = []
    proc = FillerProcessor(b"\0\0" * 480, 24000, after_ms=30,
                           on_event=lambda name, **f: fired.append(f.get("reason")), **kwargs)  # fmt: skip
    await run_test(proc, frames_to_send=frames, expected_down_frames=None)
    return fired


async def test_timer_needs_active():
    frames = [UserStoppedSpeakingFrame(), SleepFrame(0.1)]
    assert await _triggers(frames) == ["timer"]
    assert await _triggers(frames, is_active=lambda: False) == []


async def test_interruption_stops_a_waiting_timer():
    assert await _triggers([UserStoppedSpeakingFrame(), InterruptionFrame(), SleepFrame(0.1)]) == []


async def test_stay_silent_never_triggers():
    def call(name):
        return FunctionCallInProgressFrame(function_name=name, tool_call_id="c", arguments={})

    assert await _triggers([call("stay_silent"), SleepFrame(0.05)]) == []
    assert await _triggers([call("set_timer"), SleepFrame(0.05)]) == ["tool_call"]


def _call(name):
    return FunctionCallInProgressFrame(function_name=name, tool_call_id="c", arguments={})


async def test_only_slow_tools_trigger_on_start():
    slow = {"slow_tools": ["search"]}
    assert await _triggers([_call("set_timer"), SleepFrame(0.05)], **slow) == []
    assert await _triggers([_call("search"), SleepFrame(0.05)], **slow) == ["tool_call"]
    # A quick tool still gets the timer if the reply is slow after it.
    assert await _triggers([UserStoppedSpeakingFrame(), _call("set_timer"), SleepFrame(0.1)], **slow) == ["timer"]


async def test_clips_rotate_without_repeats():
    import random

    picked = []
    proc = FillerProcessor([b"\0\0" * 48, b"\1\0" * 48, b"\2\0" * 48], 24000, after_ms=1,
                           rng=random.Random(7), on_event=lambda name, **f: picked.append(f.get("clip")))  # fmt: skip
    frames = []
    for _ in range(12):  # one clip per user turn
        frames += [UserStartedSpeakingFrame(), UserStoppedSpeakingFrame(), SleepFrame(0.02)]
    await run_test(proc, frames_to_send=frames, expected_down_frames=None)
    assert len(picked) == 12 and set(picked) == {0, 1, 2}
    assert all(a != b for a, b in zip(picked, picked[1:]))


async def test_late_bot_start_still_belongs_to_the_clip():
    # Under load audio can start after the clip's own length has passed. A wall-clock check
    # then logged the filler as first_audio_out, polluting the latency headline.
    proc = FillerProcessor(b"\0\0" * 240, 24000)  # 10 ms clip
    assert proc.claim_bot_start() is False  # no clip, so a bot start is the reply's
    call = FunctionCallInProgressFrame(function_name="set_timer", tool_call_id="c", arguments={})
    claimed = []

    async def later():
        await asyncio.sleep(0.2)  # well past the clip's length
        claimed.extend([proc.claim_bot_start(), proc.claim_bot_start()])

    task = asyncio.create_task(later())
    await run_test(proc, frames_to_send=[call, SleepFrame(0.3)], expected_down_frames=None)
    await task
    assert claimed == [True, False]


async def test_no_claim_when_the_bot_is_already_speaking():
    proc = FillerProcessor(b"\0\0" * 240, 24000)
    call = FunctionCallInProgressFrame(function_name="set_timer", tool_call_id="c", arguments={})
    await run_test(proc, frames_to_send=[BotStartedSpeakingFrame(), call, SleepFrame(0.05)],
                   expected_down_frames=None)  # fmt: skip
    assert proc.claim_bot_start() is False
