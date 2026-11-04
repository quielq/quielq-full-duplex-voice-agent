"""Agents, the builder's limits, model picks, the screen's taps, and chat history (ADR-038, ADR-039).
No bot, no audio, no spend."""

from __future__ import annotations

import asyncio
import json

import aiohttp.test_utils
import pytest

from harness.agents import DIRECT, DOCUMENT_TOOLS, AgentError, AgentStore, prompt_for, seed_documents, session_config
from harness.config import REPO_ROOT, load_config
from harness.tool_policy import SCREEN_TOOLS, ToolPolicy, ToolRule
from harness.ui import MUSIC_BUTTONS, UiServer, UiState


@pytest.fixture
def cfg():
    return load_config("mac")


@pytest.fixture
def store(tmp_path, cfg):
    (tmp_path / "documents" / "it_policy").mkdir(parents=True)
    (tmp_path / "documents" / "it_policy" / "security.md").write_text("passwords")
    return AgentStore(tmp_path, cfg.providers)


def form(**over):
    return {"name": "Helper", "model": "openai/gpt-oss-20b", "color": "#ff8fab", "documents": ["it_policy"], **over}


def test_builtins_cover_the_generalist_and_each_sample_folder(store):
    agents = store.builtins()
    assert agents["generalist"].kind == "generalist" and agents["generalist"].model == DIRECT
    sample = {p.name for p in (REPO_ROOT / "assets" / "sample_documents").iterdir() if p.is_dir()}
    linked = {d for a in agents.values() for d in a.documents}
    assert sample <= linked and "workouts" in linked
    for a in agents.values():
        assert a.color in store.colors and a.model in store.models()


def test_the_builder_saves_and_the_pick_persists(store, tmp_path):
    agent = store.save(form())
    assert agent.id == "helper" and not agent.builtin
    assert store.set_active("helper").name == "Helper"
    assert AgentStore(tmp_path, load_config("mac").providers).active().id == "helper"
    store.delete("helper")
    assert store.active().id == "generalist"


@pytest.mark.parametrize("bad, reason", [
    ({"name": ""}, "name"),
    ({"name": "x" * 40}, "name"),
    ({"color": "#123456"}, "colour"),
    ({"model": "someone/unpriced"}, "model"),
    ({"documents": []}, "document"),
    ({"documents": ["../workspace"]}, "inside"),
    ({"documents": ["/etc"]}, "inside"),
    ({"documents": ["missing"]}, "not in"),
    ({"name": "Generalist"}, "built-in"),
])  # fmt: skip
def test_the_builder_refuses(store, bad, reason):
    with pytest.raises(AgentError, match=reason):
        store.save(form(**bad))


def test_a_custom_agent_cannot_become_a_generalist(store):
    assert store.save(form(kind="generalist")).kind == "documents"


def test_a_hand_edited_bad_agent_is_skipped(store, tmp_path):
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "custom.json").write_text(json.dumps([{**form(), "id": "x", "kind": "generalist"}]))
    assert "x" not in store.all()


def test_a_fake_llm_stays_fake_whatever_the_agent(cfg, store):
    coach = store.get("coach")
    assert session_config(cfg, coach).providers["stages"]["llm"] == "fake"
    cfg.providers["stages"]["llm"] = "anthropic"
    live = session_config(cfg, coach)
    assert live.providers["stages"]["llm"] == "openrouter"
    assert live.providers["providers"]["openrouter"]["model"] == coach.model
    assert cfg.providers["stages"]["llm"] == "anthropic"  # the run's own config is untouched
    assert session_config(cfg, store.get("generalist")).providers["stages"]["llm"] == "anthropic"


def test_a_document_agent_is_told_to_stay_inside_its_documents(store):
    prompt = prompt_for(store.get("it_helpdesk"), "generalist prompt")
    assert "only from" in prompt and "it policy" in prompt and "do not guess" in prompt
    assert prompt_for(store.get("generalist"), "generalist prompt") == "generalist prompt"


def test_seeding_restores_only_missing_folders(tmp_path):
    docs = tmp_path / "documents"
    copied = seed_documents(docs)
    assert "customers/customer_list.csv" in copied
    (docs / "customers" / "customer_list.csv").unlink()
    assert seed_documents(docs) == []  # a file deleted on purpose stays deleted


# The screen's music buttons (ADR-038)


class Inner:
    def __init__(self):
        self.calls = []

    async def __call__(self, params):
        self.calls.append(params.function_name)
        await params.result_callback({"playing": False, "now_playing": "Song"})


def policy_with(rule: ToolRule):
    policy = ToolPolicy(load_config("mac").tool_policy)
    inner = Inner()
    policy.rules[rule.name] = rule
    policy._inner[rule.name] = inner
    return policy, inner


async def test_a_music_button_runs_through_the_policy():
    policy, inner = policy_with(ToolRule("stop_music", "auto", 0.0, 10.0))
    assert (await policy.run_from_screen("stop_music"))["playing"] is False
    assert inner.calls == ["stop_music"]
    assert policy.decisions[-1] == {"tool": "stop_music", "decision": "allowed", "args": {}, "by": "screen"}


@pytest.mark.parametrize("rule", [
    ToolRule("stop_music", "confirm", 0.0, 10.0),
    ToolRule("stop_music", "auto", 5.0, 10.0),
    ToolRule("stop_music", "disabled", 0.0, 10.0),
    ToolRule("send_message", "auto", 0.0, 10.0),
])  # fmt: skip
async def test_the_screen_never_skips_a_question(rule):
    policy, inner = policy_with(rule)
    assert (await policy.run_from_screen(rule.name))["status"] == "denied"
    assert inner.calls == [] and policy.decisions[-1]["decision"] == "denied_screen"


def test_the_buttons_are_screen_tools():
    assert set(MUSIC_BUTTONS.values()) | {"set_light", "set_thermostat"} == set(SCREEN_TOOLS)


@pytest.mark.parametrize("name, args", [
    ("set_light", {"room": "kitchen", "is_on": True}),
    ("set_thermostat", {"target_c": 22.5}),
    ("set_thermostat", {"target_c": 16}),
])  # fmt: skip
async def test_a_tap_runs_with_the_arguments_a_tap_can_send(name, args):
    policy, inner = policy_with(ToolRule(name, "auto", 0.0, 10.0))
    await policy.run_from_screen(name, args)
    assert inner.calls == [name] and policy.decisions[-1]["args"] == args
    assert policy.screen_tools() == [name]


@pytest.mark.parametrize("name, args", [
    ("set_light", {"room": "kitchen", "is_on": True, "brightness": 100}),  # brightness stays voice only
    ("set_light", {"room": "kitchen", "is_on": "yes"}),
    ("set_light", {"room": "", "is_on": True}),
    ("set_thermostat", {"target_c": 22.3}),
    ("set_thermostat", {"target_c": 31}),
    ("set_thermostat", {"target_c": True}),
    ("set_thermostat", {"target_c": "22"}),
    ("stop_music", {"volume": 0}),
])  # fmt: skip
async def test_a_tap_with_other_arguments_is_refused(name, args):
    policy, inner = policy_with(ToolRule(name, "auto", 0.0, 10.0))
    assert (await policy.run_from_screen(name, args))["status"] == "denied"
    assert inner.calls == [] and policy.decisions[-1]["decision"] == "denied_screen"


async def test_a_light_that_would_ask_is_refused_from_the_screen():
    policy, inner = policy_with(ToolRule("set_light", "confirm", 0.0, 10.0))
    assert (await policy.run_from_screen("set_light", {"room": "kitchen", "is_on": True}))["status"] == "denied"
    assert policy.screen_tools() == [] and inner.calls == []


# The model picked for a session (ADR-040)


def test_a_model_pick_is_kept_per_agent(store, tmp_path, cfg):
    coach = store.set_active("coach", "deepseek/deepseek-v4.1-flash")
    assert coach.model == "deepseek/deepseek-v4.1-flash"
    again = AgentStore(tmp_path, cfg.providers)
    assert again.active().model == "deepseek/deepseek-v4.1-flash"
    assert again.get("coach").model == "anthropic/claude-haiku-5.5"  # the agent itself is unchanged
    again.set_active("generalist")
    assert again.picks() == {"coach": "deepseek/deepseek-v4.1-flash"} and again.active().model == DIRECT
    again.set_active("coach", None)
    assert again.picks() == {} and again.active().model == "anthropic/claude-haiku-5.5"


def test_an_unlisted_model_is_refused_and_a_bad_file_is_ignored(store, tmp_path):
    with pytest.raises(AgentError, match="model"):
        store.set_active("coach", "someone/unpriced")
    (tmp_path / "agents" / "active.json").parent.mkdir(exist_ok=True)
    (tmp_path / "agents" / "active.json").write_text(json.dumps({"id": "coach", "models": {"coach": "someone/unpriced"}}))
    assert store.active().model == "anthropic/claude-haiku-5.5"


def test_the_generalist_can_run_on_a_picked_model_live_only(cfg, store):
    gen = store.with_model(store.get("generalist"), "qwen/qwen3.8-flash")
    assert session_config(cfg, gen).providers["stages"]["llm"] == "fake"
    cfg.providers["stages"]["llm"] = "anthropic"
    live = session_config(cfg, gen).providers
    assert live["stages"]["llm"] == "openrouter" and live["providers"]["openrouter"]["model"] == "qwen/qwen3.8-flash"


# Chat history and the agent routes


def test_history_has_one_entry_per_turn_and_reply(tmp_path):
    ui = UiState(tmp_path)
    ui.on_event({"event": "asr_final", "text": "what time is it"})
    v = ui.history_version
    ui.on_text("It is ")
    ui.on_text("noon.")
    assert ui.history_version == v and ui.history_snapshot()[-1]["who"] == "user"  # streaming: no event yet
    ui.on_event({"event": "audio_silent"})
    assert ui.history_version == v + 1
    assert ui.history_snapshot()[-1] == {"who": "agent", "text": "It is noon."}


def test_history_keeps_the_last_turns(tmp_path):
    ui = UiState(tmp_path)
    for i in range(60):
        ui.on_event({"event": "user_text", "text": f"turn {i}"})
    assert len(ui.history_snapshot()) == 40 and ui.history_snapshot()[-1]["text"] == "turn 59"


async def _client(tmp_path, store=None):
    server = UiServer(UiState(tmp_path), port=0)
    server.agents = store
    client = aiohttp.test_utils.TestClient(aiohttp.test_utils.TestServer(server._app()))
    await client.start_server()
    return server, client


async def test_picking_an_agent_ends_the_session(tmp_path, store):
    server, client = await _client(tmp_path, store)
    ended = []

    async def switch():
        ended.append(1)

    server.on_switch = switch
    try:
        r = await client.post("/api/agents/active", json={"id": "coach"})
        assert r.status == 200 and ended == [1] and server.state.switching == "Coach"
        server.state.set_agent(store.active())
        assert (await client.post("/api/agents/active", json={"id": "coach"})).status == 200 and ended == [1]
        r = await client.post("/api/agents/active", json={"id": "coach", "model": "openai/gpt-oss-20b"})
        assert r.status == 200 and ended == [1, 1]  # same agent, another model: a new session
        bad = await client.post("/api/agents/active", json={"id": "coach", "model": "someone/unpriced"})
        assert bad.status == 400
        listing = await (await client.get("/api/agents")).json()
        assert {"generalist", "coach"} <= {a["id"] for a in listing["agents"]}
        assert listing["picks"] == {"coach": "openai/gpt-oss-20b"}
        assert "it_policy/" in listing["documents"]
        evil = await client.post("/api/agents/active", json={"id": "coach"}, headers={"Origin": "https://evil.example"})
        assert evil.status == 403
    finally:
        await client.close()


async def test_the_builder_route_reports_why_it_refused(tmp_path, store):
    _, client = await _client(tmp_path, store)
    try:
        r = await client.post("/api/agents/save", json=form(color="#000000"))
        assert r.status == 400 and "colour" in await r.text()
        assert (await client.post("/api/agents/save", json=form())).status == 200
        assert (await client.post("/api/agents/delete", json={"id": "generalist"})).status == 400
    finally:
        await client.close()


async def test_taps_need_a_session_and_pass_only_their_arguments(tmp_path):
    server, client = await _client(tmp_path)
    try:
        assert (await client.post("/api/music", json={"action": "pause"})).status == 409
        assert (await client.post("/api/light", json={"room": "kitchen", "is_on": True})).status == 409
        assert (await client.post("/api/music", json={"action": "rm"})).status == 400
        assert (await client.post("/api/thermostat", data="[]")).status == 400
        calls = []

        async def tap(tool, args):
            calls.append((tool, args))
            return {"playing": tool != "stop_music", "now_playing": "Song"} if "music" in tool or "track" in tool else {}

        server.on_screen_tool = tap
        assert (await client.post("/api/music", json={"action": "next"})).status == 200
        assert server.state.music == {"playing": True, "now_playing": "Song"}
        await client.post("/api/light", json={"room": "kitchen", "is_on": False, "brightness": 5})
        await client.post("/api/thermostat", json={"target_c": 23.5, "mode": "heat"})
        assert calls[1:] == [("set_light", {"room": "kitchen", "is_on": False}),
                             ("set_thermostat", {"target_c": 23.5})]  # fmt: skip
        evil = await client.post("/api/light", json={"room": "kitchen", "is_on": True}, headers={"Origin": "https://evil.example"})
        assert evil.status == 403
    finally:
        await client.close()


def test_history_is_its_own_event(tmp_path):
    from tests.test_ui import _with_server

    async def body(server, session, base):
        async with session.get(base + "/events") as stream:
            await asyncio.wait_for(stream.content.readline(), 5)  # the state, then a blank line
            await stream.content.readline()
            assert await asyncio.wait_for(stream.content.readline(), 5) == b"event: history\n"
            assert json.loads((await stream.content.readline()).removeprefix(b"data: ")) == []
            server.state.on_event({"event": "user_text", "text": "hello"})
            while (line := await asyncio.wait_for(stream.content.readline(), 5)) != b"event: history\n":
                pass
            assert json.loads((await stream.content.readline()).removeprefix(b"data: ")) == [
                {"who": "user", "text": "hello"}
            ]

    asyncio.run(_with_server(tmp_path, body))


def test_document_agents_get_only_read_tools():
    from servers.tasks.docs import server as docs

    assert set(DOCUMENT_TOOLS) <= set(dir(docs))


def test_a_document_agent_reads_a_table_for_the_latest_entry(store):
    prompt = prompt_for(store.get("coach"), "")
    assert "list_documents" in prompt and "rather than searching for dates" in prompt
