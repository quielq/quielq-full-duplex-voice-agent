"""Network-loss detection (ADR-037), on fakes: provider outages that fail to connect, then recover.

No provider is called. The reachability probe is a fake except in the TCP tests, which only
connect to a socket on this machine.
"""

import asyncio
import json
import socket

import pytest
from pipecat.frames.frames import ErrorFrame, OutputAudioRawFrame, TTSAudioRawFrame
from pipecat.observers.base_observer import FramePushed
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.tests.utils import run_test

from harness.bot import run_bot
from harness.config import load_config
from harness.fakes import RuleBasedLLMService, ScriptedSTTService, ToneTTSService
from harness.mcp_tools import TASK_SERVERS
from harness.network import ClipPlayer, NetworkWatch, stage_hosts, stage_of, tcp_reachable
from harness.ui import UiState

HOSTS = {"stt": ("stt.test", 443), "llm": ("llm.test", 443), "tts": ("tts.test", 443)}


class FakeNet:
    """A network that is up or down, and a probe that reports it, counting calls."""

    def __init__(self):
        self.down = False
        self.probes = 0

    async def probe(self, host, port, timeout_s):
        self.probes += 1
        return not self.down


def watch(net, **kwargs):
    events, played = [], []

    async def play():
        played.append("network_lost")

    w = NetworkWatch(HOSTS, play=play, probe=net.probe, attempts=1, backoff_s=[0.05],
                     on_event=lambda name, **f: events.append({"event": name, **f}), **kwargs)  # fmt: skip
    return w, events, played


def names(events):
    return [e["event"] for e in events]


# The probe


async def test_tcp_probe_sees_a_listening_port_and_a_closed_one():
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    assert await tcp_reachable("127.0.0.1", port, 1.0)
    server.close()
    await server.wait_closed()
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    closed = s.getsockname()[1]
    s.close()
    assert not await tcp_reachable("127.0.0.1", closed, 1.0)


def test_each_cloud_stage_probes_its_provider_and_fakes_probe_nothing():
    assert stage_hosts({"stt": "fake", "llm": "fake", "tts": "fake"}) == {}
    hosts = stage_hosts({"stt": "voice_listen", "llm": "anthropic", "tts": "voice_speak", "music": "spotify"})
    assert hosts == {"stt": ("api.deepgram.com", 443), "llm": ("api.anthropic.com", 443),
                     "tts": ("api.cartesia.ai", 443)}  # fmt: skip
    assert stage_hosts({"stt": "deepgram_flux", "tts": "cartesia", "llm": "groq"})["llm"][0] == "api.groq.com"


def test_errors_are_traced_to_their_stage():
    assert stage_of(ScriptedSTTService(script=["x"])) == "stt"
    assert stage_of(RuleBasedLLMService()) == "llm"
    assert stage_of(ToneTTSService()) == "tts"
    assert stage_of(FrameProcessor()) is None


# The watch


async def test_one_bad_request_is_not_an_outage():
    net = FakeNet()
    w, events, played = watch(net)
    assert await w.check("llm", "400 bad request") is False
    assert names(events) == ["provider_error"] and events[0]["reachable"] is True
    assert not played and not w.down


async def test_an_outage_speaks_once_then_logs_the_recovery():
    net = FakeNet()
    lost_turns = []
    w, events, played = watch(net, on_turn_lost=lost_turns.append)
    net.down = True
    assert await w.check("tts", "connect failed") is True
    assert w.down and played == ["network_lost"]
    # More failures in the same outage, from any stage, stay quiet.
    assert await w.check("stt", "connect failed") is True
    assert await w.check("llm", "connect failed") is True
    assert played == ["network_lost"]
    assert lost_turns == ["tts", "stt", "llm"]
    await asyncio.sleep(0.2)
    assert w.down  # still probing
    net.down = False
    await asyncio.sleep(0.2)
    assert not w.down
    restored = [e for e in events if e["event"] == "network_restored"]
    assert len(restored) == 1 and restored[0]["stages"] == ["llm", "stt", "tts"]
    assert restored[0]["down_ms"] > 0
    # A second outage is a new outage, so it is said again, once.
    net.down = True
    await w.check("llm", "connect failed")
    assert played == ["network_lost", "network_lost"] and w.outages == 2
    await w.close()


async def test_errors_at_the_same_moment_share_one_probe():
    net = FakeNet()
    net.down = True
    w, events, played = watch(net)
    results = await asyncio.gather(w.check("llm", "a"), w.check("llm", "b"))
    assert results == [True, True] and net.probes == 1
    assert names(events).count("network_lost") == 1 and played == ["network_lost"]
    await w.close()


async def test_a_stage_with_no_host_is_never_an_outage():
    net = FakeNet()
    net.down = True
    w = NetworkWatch({}, probe=net.probe)
    assert await w.check("llm", "x") is False and net.probes == 0


async def test_an_error_frame_from_a_service_starts_the_check():
    net = FakeNet()
    net.down = True
    w, events, played = watch(net)
    frame = ErrorFrame("Cartesia websocket connect failed")
    await w.on_push_frame(FramePushed(source=ToneTTSService(), destination=FrameProcessor(), frame=frame,
                                      direction=FrameDirection.UPSTREAM, timestamp=0))  # fmt: skip
    assert [e["stage"] for e in events if e["event"] == "network_lost"] == ["tts"]
    await w.close()


# The clip player


async def test_the_clip_is_raw_audio_and_other_output_is_held_back_while_down():
    reply = TTSAudioRawFrame(b"\x01\x00" * 320, 16000, 1)
    up = ClipPlayer(16000, muted=lambda: False)
    down, _ = await run_test(up, frames_to_send=[reply])
    assert [f.audio for f in down if isinstance(f, OutputAudioRawFrame)] == [reply.audio]

    player = ClipPlayer(16000, muted=lambda: True)

    class Trigger(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, TTSAudioRawFrame):
                await player.play("network_lost")  # the outage starts as a reply frame arrives
            await self.push_frame(frame, direction)

    down, _ = await run_test(Pipeline([Trigger(), player]), frames_to_send=[reply])
    audio = [f for f in down if isinstance(f, OutputAudioRawFrame)]
    # Only the clip comes out: plain output audio, no TTS frame and no text, so nothing for the
    # context to keep. The reply frame behind it is held back.
    assert audio and not any(isinstance(f, TTSAudioRawFrame) for f in audio)
    assert reply.audio not in [f.audio for f in audio]
    assert player.played == ["network_lost"]


# The whole bot, on fakes


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    cfg = load_config("mac")
    cfg.providers["stages"] = {"stt": "fake", "llm": "fake", "tts": "fake"}
    cfg.profile["paths"] = {"logs_dir": str(tmp_path / "logs"), "data_dir": str(tmp_path / "data")}
    cfg.profile["network"].update(backoff_s=[0.1], probe_attempts=1)
    monkeypatch.setitem(__import__("harness.network").network.STAGE_HOSTS, "fake", ("provider.test", 443))
    return cfg


class OfflineLLM(RuleBasedLLMService):
    """The Wi-Fi drops as one request is sent, and comes back soon after."""

    def __init__(self, net: FakeNet, drop_on: str, back_after_s: float, **kwargs):
        super().__init__(**kwargs)
        self._net = net
        self._drop_on = drop_on
        self._back_after_s = back_after_s

    async def _respond(self, context):
        last = context.get_messages()[-1]
        if last.get("role") == "user" and self._drop_on in str(last.get("content")):
            self._net.down = True
            asyncio.get_running_loop().call_later(self._back_after_s, setattr, self._net, "down", False)
        if self._net.down:
            await self.push_error(error_msg="Cannot connect to host", exception=ConnectionError("unreachable"))
            return
        await super()._respond(context)


def test_an_llm_outage_says_the_phrase_once_and_the_next_turn_works(cfg, tmp_path, monkeypatch):
    net = FakeNet()
    monkeypatch.setattr("harness.network.tcp_reachable", net.probe)
    llm = OfflineLLM(net, drop_on="two minutes", back_after_s=0.3)
    monkeypatch.setattr("harness.bot.build_llm", lambda *a, **k: llm)
    turns = ["set a timer for one minute", "set a timer for two minutes", 1.0, "set a timer for three minutes"]
    result = asyncio.run(
        run_bot(cfg, input_mode="text", turns=turns, data_dir=str(tmp_path / "data"), reply_timeout_s=10,
                handle_sigint=False, servers={"clock": TASK_SERVERS["clock"]})  # fmt: skip
    )
    evs = [json.loads(line) for line in open(result.events_path)]
    seq = names(evs)
    assert [e["phrase"] for e in evs if e["event"] == "phrase_played"] == ["network_lost"]
    assert seq.count("network_lost") == 1 and seq.count("network_restored") == 1
    assert seq.index("network_lost") < seq.index("network_restored")
    assert "llm_error_spoken" not in seq and "reply_timeout" not in seq
    # The lost request was dropped, and the turn after recovery ran its tool and replied.
    assert [e["text"] for e in evs if e["event"] == "lost_turn_dropped"] == ["set a timer for two minutes"]
    assert [c["args"] for c in result.tool_calls] == [{"duration_seconds": 60, "label": ""}, {"duration_seconds": 180, "label": ""}]
    assert seq.index("network_restored") < [i for i, n in enumerate(seq) if n == "tool_call_end"][-1]
    # The phrase never entered the conversation.
    context = json.dumps(result.context_messages).lower()
    assert "connection" not in context and "two minutes" not in context
    # The face goes to its lost-connection state and back.
    ui = UiState(tmp_path / "data")
    states = []
    for e in evs:
        ui.on_event(e)
        if e["event"] in ("network_lost", "network_restored"):
            states.append((ui.snapshot()["network_down"], ui.face()))
    # Back up, it may still look sad for a moment from the error itself (the usual mood hold).
    assert states[0] == (True, "sad") and states[1][0] is False


def test_the_face_holds_the_outage_until_the_network_is_back(tmp_path):
    ui = UiState(tmp_path)
    ui.on_event({"event": "network_lost", "stage": "stt"})
    ui.on_event({"event": "first_audio_out"})  # audio alone does not mean the network is back
    assert ui.snapshot()["network_down"] and ui.face() == "sad"
    ui.on_event({"event": "network_restored", "stages": ["stt"], "down_ms": 4000})
    assert not ui.snapshot()["network_down"]


def test_one_command_makes_the_recovery_clips_later():
    from bench.make_phrases import recovery_phrases
    from harness.phrase_cache import PHRASES

    assert recovery_phrases() == ["network_lost", "cap_reached"]
    assert PHRASES["network_lost"] == "I've lost my connection. Give me a moment."
    for profile in ("mac", "pi"):
        r = load_config(profile).profile["recovery"]
        assert (r["network_clip"], r["cap_clip"]) == ("network_lost", "cap_reached")
