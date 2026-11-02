"""The face follows the event log, and the page server stays on this machine (no bot, no audio)."""

import asyncio
import json

import aiohttp
import pytest

from harness.bot import SYSTEM_PROMPT
from harness.config import load_config
from harness.ui import PAGE, UiServer, UiState, read_devices, step_label


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def ui(tmp_path, clock):
    return UiState(tmp_path, clock)


def send(ui, event, **fields):
    ui.on_event({"event": event, **fields})


def test_a_turn_moves_through_the_faces(ui):
    assert ui.face() == "neutral"
    send(ui, "vad_onset")
    assert ui.face() == "listening"
    send(ui, "asr_final", text="turn on the lights")
    assert ui.face() == "thinking" and ui.heard == "turn on the lights"
    send(ui, "tool_call_start", name="write_file", args={"path": "site/index.html"})
    assert ui.face() == "working" and ui.step == "Writing site/index.html"
    send(ui, "tool_call_end", name="write_file", result={"bytes": 10})
    send(ui, "first_audio_out")
    assert ui.snapshot()["talking"] is True and ui.face() == "happy"
    send(ui, "audio_silent")
    assert ui.snapshot()["talking"] is False


def test_a_mood_fades_back_to_neutral(ui, clock):
    send(ui, "tool_call_end", name="get_time", result='{"time": "10:00"}')
    send(ui, "audio_silent")
    ui.activity = "idle"
    assert ui.face() == "happy"
    clock.now += 10
    assert ui.face() == "neutral"


@pytest.mark.parametrize("result", ['{"error": "no file"}', {"status": "denied"}, {"status": "not_done"}])
def test_a_failed_tool_looks_sad(ui, result):
    send(ui, "tool_call_end", name="read_file", result=result)
    ui.activity = "idle"
    assert ui.face() == "sad"


@pytest.mark.parametrize("status", ["waiting_for_undo", "needs_confirmation"])
def test_a_held_tool_is_not_a_success(ui, status):
    send(ui, "tool_call_end", name="bash", result={"status": status})
    ui.activity = "idle"
    assert ui.face() == "neutral"


def test_a_refused_command_looks_angry(ui):
    send(ui, "tool_policy", decision="denied_guard")
    assert ui.face() == "angry"


def test_a_spoken_question_waits_for_an_answer(ui):
    send(ui, "tool_policy", decision="read_back")
    assert ui.face() == "asking"


def test_the_face_keeps_waiting_after_the_question_has_played(ui):
    send(ui, "tool_policy", decision="read_back")
    send(ui, "first_audio_out")
    send(ui, "audio_silent")
    assert ui.face() == "asking"
    send(ui, "tool_policy", decision="confirmed")
    send(ui, "first_audio_out")
    send(ui, "audio_silent")
    assert ui.face() != "asking"


def test_an_apology_looks_sad(ui):
    ui.on_text("Sorry, ")
    ui.on_text("I missed that.")
    assert ui.face() == "sad" and ui.said == "Sorry, I missed that."


def test_a_new_turn_clears_the_last_reply(ui):
    ui.on_text("First.")
    send(ui, "asr_final", text="and then")
    ui.on_text("Second.")
    assert ui.said == "Second."


def test_mute_wins_over_everything(ui):
    send(ui, "vad_onset")
    send(ui, "mic_mute", muted=True)
    assert ui.face() == "muted"


def test_music_comes_from_tool_results(ui):
    send(ui, "tool_call_end", name="play_music", result={"playing": True, "now_playing": "Dive"})
    assert ui.music == {"playing": True, "now_playing": "Dive"}
    send(ui, "tool_call_end", name="stop_music", result={"playing": False})
    assert ui.music["playing"] is False


def test_devices_default_until_a_mock_server_writes(tmp_path):
    devices = read_devices(tmp_path, 0)
    assert {l["room"] for l in devices["lights"]} == {"bedroom", "kitchen", "living room"}
    assert devices["thermostat"]["target_c"] == 24.0
    (tmp_path / "thermostat.json").write_text(json.dumps({"target_c": 21.5, "mode": "cool"}))
    assert read_devices(tmp_path, 0)["thermostat"]["target_c"] == 21.5


def test_only_future_timers_show(tmp_path):
    (tmp_path / "clock.json").write_text(json.dumps({
        "a": {"kind": "timer", "label": "old", "fires_at": 5},
        "b": {"kind": "timer", "label": "tea", "fires_at": 50},
    }))  # fmt: skip
    assert [t["label"] for t in read_devices(tmp_path, 10)["timers"]] == ["tea"]


def test_a_step_label_survives_missing_arguments():
    assert step_label("write_file", {}) == "Writing"
    assert step_label("set_alarm", {}) == "Set alarm"


def test_the_page_makes_no_outside_requests():
    page = PAGE.read_text()
    assert "http://" not in page.replace('xmlns="http://www.w3.org/2000/svg"', "") and "https://" not in page


@pytest.mark.parametrize("profile", ["mac", "pi"])
def test_the_ui_stays_on_this_machine_by_default(profile):
    ui = load_config(profile).profile["ui"]
    assert ui["enabled"] is False and ui["host"] == "127.0.0.1"


def test_the_prompt_tells_the_model_to_finish_a_long_job():
    assert "keep calling tools until it is finished" in SYSTEM_PROMPT


async def _with_server(tmp_path, body):
    server = UiServer(UiState(tmp_path), port=0)
    await server.start()
    port = server._runner.addresses[0][1]
    try:
        async with aiohttp.ClientSession() as session:
            return await body(server, session, f"http://127.0.0.1:{port}")
    finally:
        await server.stop()


def test_the_page_and_first_state_are_served(tmp_path):
    async def body(_server, session, base):
        async with session.get(base + "/") as page:
            assert "Robot face" in await page.text()
        async with session.get(base + "/events") as stream:
            line = await asyncio.wait_for(stream.content.readline(), 5)
            assert json.loads(line.removeprefix(b"data: "))["face"] == "neutral"

    asyncio.run(_with_server(tmp_path, body))


def test_another_site_cannot_mute_the_mic(tmp_path):
    async def body(server, session, base):
        calls = []
        server.on_mute = lambda: calls.append(1)
        async with session.post(base + "/api/mute", headers={"Origin": "https://evil.example"}) as r:
            assert r.status == 403
        assert not calls
        async with session.post(base + "/api/mute") as r:
            assert r.status == 200
        assert calls == [1]

    asyncio.run(_with_server(tmp_path, body))


def test_mute_without_a_mic_says_so(tmp_path):
    async def body(_server, session, base):
        async with session.post(base + "/api/mute") as r:
            assert r.status == 409

    asyncio.run(_with_server(tmp_path, body))
