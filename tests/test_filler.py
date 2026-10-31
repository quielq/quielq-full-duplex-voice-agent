"""ADR-009 waiting phrase, on real Silero VAD with fake STT, LLM, and TTS. No spend."""

import asyncio
import json
import shutil
import subprocess

import pytest

from pipecat.frames.frames import (
    FunctionCallInProgressFrame,
    InterruptionFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.tests.utils import SleepFrame, run_test

from bench.latency_probe import filler_rate, stage_deltas
from harness import bot
from harness.bot import run_bot
from harness.config import load_config
from harness.fakes import RuleBasedLLMService
from harness.filler import FillerProcessor
from harness.phrase_cache import PHRASES, load_phrase

pytestmark = pytest.mark.skipif(not shutil.which("say"), reason="macOS `say` not available")


@pytest.fixture
def cfg(tmp_path):
    cfg = load_config("mac")
    cfg.providers["stages"] = {"stt": "fake", "llm": "fake", "tts": "fake"}
    cfg.profile["paths"] = {"logs_dir": str(tmp_path / "logs"), "data_dir": str(tmp_path / "data")}
    cfg.profile["filler"]["enabled"] = True
    return cfg


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    path = tmp_path_factory.mktemp("audio") / "request.wav"
    subprocess.run(["say", "-o", str(path), "--data-format=LEI16@16000", "Set a timer for one minute."], check=True)
    return path


class SlowLLM(RuleBasedLLMService):
    async def _respond(self, context):
        await asyncio.sleep(1.0)
        await super()._respond(context)


async def _run(cfg, tmp_path, clip, text):
    r = await run_bot(cfg, input_mode="wav", wav=clip, say=text, data_dir=str(tmp_path / "d"),
                      handle_sigint=False, reply_timeout_s=30)  # fmt: skip
    return r, [json.loads(line) for line in r.events_path.read_text().splitlines()]


def named(events, name):
    return [e for e in events if e["event"] == name]


def test_off_in_both_profiles():
    for profile in ("mac", "pi"):
        assert load_config(profile).profile["filler"]["enabled"] is False


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
    assert named(ev, "filler_clip")[0]["real"] is False
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
