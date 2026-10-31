"""No-wake-word filters: pure logic, then each layer in the bot on fakes."""

import asyncio
import json
import math
import struct

import pytest

from bench.task_runner import load_cases, run_case
from harness.addressing import Addressing, ConversationState, LevelGate, MicGate, looks_addressed, rms_db
from harness.bot import SYSTEM_PROMPT, run_bot
from harness.config import load_config
from harness.mcp_tools import TASK_SERVERS
from pipecat.frames.frames import InputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection

VERBS = load_config("mac").profile["addressing"]["command_verbs"]


def cfg_for(tmp_path):
    cfg = load_config("mac")
    cfg.providers["stages"] = {"stt": "fake", "llm": "fake", "tts": "fake"}
    cfg.profile["paths"] = {"logs_dir": str(tmp_path / "logs"), "data_dir": str(tmp_path / "data")}
    return cfg


@pytest.mark.parametrize(
    "text,why",
    [
        ("what time is it", "question"),
        ("okay so what time is it", "question"),
        ("Is the oven on?", "question"),
        ("it is raining right?", "question"),
        ("please dim the bedroom lights", "polite"),
        ("set a timer for ten minutes", "command"),
        ("alright, cancel my timer now", "command"),
        ("yeah", None),
        ("mm hmm", None),
        ("no way", None),
        ("I think it might rain", None),
        ("the meeting was fine", None),
    ],
)
def test_idle_bar(text, why):
    assert looks_addressed(text, min_words=3, command_verbs=VERBS, names=[]) == why


def test_a_name_clears_the_bar_at_any_length():
    assert looks_addressed("Ada", min_words=3, command_verbs=VERBS, names=["ada"]) == "name"
    assert looks_addressed("Adam left", min_words=3, command_verbs=VERBS, names=["ada"]) is None


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


def test_state_goes_idle_after_the_window_and_not_during_speech():
    clock, events = Clock(), []
    state = ConversationState(30, clock=clock, on_event=lambda name, **f: events.append(f["state"]))
    assert not state.active
    state.touch()
    clock.now += 29
    assert state.active
    clock.now += 2
    assert not state.active
    state.bot_speaking(True)
    clock.now += 500
    assert state.active  # a long reply
    state.bot_speaking(False)
    clock.now += 29
    assert state.active  # the window starts when speech ends
    assert events == ["ACTIVE", "IDLE", "ACTIVE"]


def test_wake_fallback_raises_the_idle_bar():
    a = dict(load_config("mac").profile["addressing"], require_wake_after_idle=True)
    addressing = Addressing(a, ConversationState(30))
    assert not addressing.admit("set a timer for ten minutes")
    assert addressing.admit("hey assistant set a timer for ten minutes")
    assert addressing.admit("and another one")  # ACTIVE now


def pcm(level_db, ms=20, rate=16000, tone=True):
    amp = 32768 * 10 ** (level_db / 20) * math.sqrt(2)
    n = rate * ms // 1000
    return struct.pack(f"<{n}h", *(int(amp * math.sin(i * 0.3)) if tone else 0 for i in range(n)))


def test_rms_db_reads_a_known_level():
    assert rms_db(pcm(-30)) == pytest.approx(-30, abs=0.3)
    assert rms_db(b"") == -120


def test_gate_tracks_the_floor_and_needs_sustained_level():
    gate = LevelGate(margin_db=12, open_ms=100, close_ms=200)
    for _ in range(30):
        gate.update(-45, 20)  # the room, measured at startup
    assert gate.floor_db == -45
    assert not gate.open
    gate.update(-20, 20)
    assert not gate.open  # a click
    for _ in range(5):
        gate.update(-20, 20)
    assert gate.open
    floor = gate.floor_db
    for _ in range(9):
        gate.update(-20, 20)
    assert gate.floor_db == floor  # speech must not raise the floor
    for _ in range(10):
        gate.update(-45, 20)
    assert not gate.open


def test_a_new_steady_noise_becomes_the_floor():
    gate = LevelGate(margin_db=12, open_ms=100, close_ms=200, calibrate_ms=0, start_floor_db=-60, reseat_ms=2000)
    for _ in range(50):
        gate.update(-30, 20)
    assert gate.open  # could still be someone talking
    for _ in range(50):
        gate.update(-30, 20)
    assert gate.floor_db == -30 and not gate.open


def run_gate(blocks, *, active=False, muted=False):
    gate = LevelGate(margin_db=12, open_ms=60, close_ms=100, start_floor_db=-50, calibrate_ms=0)
    sent = []
    mic = MicGate(gate, lambda: active, pre_roll_ms=100, sample_rate=16000)
    if muted:
        mic.toggle_mute()

    async def push(frame, direction=FrameDirection.DOWNSTREAM):
        sent.append(frame)

    mic.push_frame = push

    async def run():
        for level in blocks:
            frame = InputAudioRawFrame(audio=pcm(level), sample_rate=16000, num_channels=1)
            await mic.process_frame(frame, FrameDirection.DOWNSTREAM)

    asyncio.run(run())
    return sent, mic


QUIET, LOUD = -60, -20


def test_gate_withholds_quiet_audio_and_keeps_the_pre_roll():
    sent, mic = run_gate([QUIET] * 50)
    assert sent == []
    assert mic.withheld_ms == pytest.approx(900, abs=40)  # all but the pre-roll
    sent, _ = run_gate([QUIET] * 50 + [LOUD] * 5)
    # Opens on the third loud block, which arrives after 100 ms of pre-roll.
    assert len(sent) == 5 + 3
    assert rms_db(sent[0].audio) < -50 and rms_db(sent[-1].audio) > -25


def test_gate_never_blocks_active_dialogue():
    sent, _ = run_gate([QUIET] * 20, active=True)
    assert len(sent) == 20


def test_mute_stops_everything_even_when_active():
    sent, _ = run_gate([LOUD] * 20, active=True, muted=True)
    assert sent == []


def bot(tmp_path, turns, **addressing):
    cfg = cfg_for(tmp_path)
    cfg.profile["addressing"] |= {"enabled": True, **addressing}
    return asyncio.run(
        run_bot(cfg, input_mode="text", turns=turns, data_dir=str(tmp_path / "data"), reply_timeout_s=5,
                handle_sigint=False, servers={"clock": TASK_SERVERS["clock"]})  # fmt: skip
    )


def event_names(result):
    return [json.loads(line)["event"] for line in open(result.events_path)]


def test_stay_silent_ends_the_turn_with_no_speech_and_no_second_call(tmp_path):
    result = bot(tmp_path, ["could you pass me the salt"])
    names = event_names(result)
    assert "stay_silent" in names and "reply_timeout" not in names
    assert names.count("llm_request") == 1
    assert result.spoken_text == ""


def test_a_dropped_utterance_never_reaches_the_llm(tmp_path):
    result = bot(tmp_path, ["yeah right"])
    names = event_names(result)
    assert "addressing_drop" in names and "llm_request" not in names


def test_addressing_off_offers_no_tool_and_changes_nothing(tmp_path):
    cfg = cfg_for(tmp_path)
    assert cfg.profile["addressing"]["enabled"] is False
    result = asyncio.run(
        run_bot(cfg, input_mode="text", say="could you pass me the salt", data_dir=str(tmp_path / "data"),
                reply_timeout_s=5, handle_sigint=False, servers={"clock": TASK_SERVERS["clock"]})  # fmt: skip
    )
    assert "stay_silent" not in event_names(result)
    assert "I heard" in result.spoken_text


def test_prompt_mentions_stay_silent_only_when_on():
    assert "stay_silent" not in SYSTEM_PROMPT


@pytest.mark.parametrize("case", [c for c in load_cases() if c.addressing], ids=lambda c: c.id)
def test_addressing_cases_pass_on_fakes(tmp_path, case):
    result = asyncio.run(run_case(cfg_for(tmp_path), case))
    assert result.passed, f"{result.bucket}: {result.detail}"


def wav_run(tmp_path, script, *, gate):
    from bench.interruption_test import _say
    from harness.file_input import Segment

    cfg = cfg_for(tmp_path)
    cfg.profile["addressing"] |= {"enabled": True}
    cfg.profile["addressing"]["gate"]["enabled"] = gate
    clip = _say("Set a timer for ninety seconds.", tmp_path / "clip.wav")
    return asyncio.run(
        run_bot(cfg, input_mode="wav", segments=[Segment(clip)], stt_script=script,
                data_dir=str(tmp_path / "data"), reply_timeout_s=8, handle_sigint=False,
                servers={"clock": TASK_SERVERS["clock"]})  # fmt: skip
    )


@pytest.mark.parametrize("gate", [False, True])
def test_real_vad_path_answers_a_request(tmp_path, gate):
    result = wav_run(tmp_path, ["set a timer for 90 seconds"], gate=gate)
    assert [c["name"] for c in result.tool_calls] == ["set_timer"], event_names(result)


def test_real_vad_path_drops_a_fragment_before_the_llm(tmp_path):
    result = wav_run(tmp_path, ["yeah right"], gate=False)
    names = event_names(result)
    assert "addressing_drop" in names and "llm_request" not in names
