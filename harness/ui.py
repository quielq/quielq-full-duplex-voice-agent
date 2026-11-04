"""The robot face and device panel, served to a browser on the same machine (ADR-027).

``UiState`` turns event-log lines into what the page shows, so the UI sees exactly what the logs
record and adds no second path through the pipeline. ``UiServer`` sends that state over
server-sent events. The page is one self-contained file with no outside requests, so the face
still works when the network does not. The Spotify player is a separate file, loaded only when
``ui.spotify_player`` is on (ADR-031).

Lights and the thermostat are read from the same JSON files their mock servers write. The
controls are the mic mute, the music buttons, a tap on a light to turn it on or off, the
thermostat's step buttons, and the agent picker and builder (ADR-038, ADR-039). A tap runs its
tool through ``ToolPolicy.run_from_screen``, which refuses anything the voice path would hold
or ask about, so the screen never skips a question.

The page gets two kinds of server-sent event: the state, whenever it changes, and the chat
history, only when a turn is added. Streaming reply text so never resends the history.

    uv run python -m harness.ui --demo     # every expression in turn, no bot, no spend
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import time
from collections import deque
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from aiohttp import web

from harness.agents import Agent, AgentError, AgentStore
from harness.config import REPO_ROOT, load_config
from servers.tasks.music.backends import MusicError
from servers.tasks.lights.server import DEFAULT as DEFAULT_LIGHTS
from servers.tasks.thermostat.server import DEFAULT as DEFAULT_THERMOSTAT

PAGE = Path(__file__).with_name("ui_page.html")
PLAYER_SCRIPT = Path(__file__).with_name("ui_spotify.js")
MOOD_HOLD_S = 3.0
HISTORY_TURNS = 40
HISTORY_CHARS = 600
TIMERS_SHOWN = 4
# The screen's music buttons. Every tool a tap may run is in tool_policy.SCREEN_TOOLS (ADR-038).
MUSIC_BUTTONS = {"pause": "stop_music", "play": "resume_music", "next": "next_track"}
THERMOSTAT_STEP_C = 0.5
LOCAL_HOSTS = {"127.0.0.1", "localhost"}
SORRY_WORDS = ("sorry", "i can't", "i cannot", "i couldn't", "unfortunately")
ANGRY_DECISIONS = {"denied_guard", "denied_contained", "contained"}
ASKING_DECISIONS = {"hold_needs_confirmation", "read_back"}
STEP_LABELS = {
    "write_file": "Writing {path}",
    "edit_file": "Editing {path}",
    "read_file": "Reading {path}",
    "list_files": "Looking through files",
    "grep": "Searching files",
    "bash": "Running a command",
    "serve": "Managing a service",
    "open_in_browser": "Opening {path}",
    "search": "Searching the web",
    "fetch_content": "Reading a web page",
    "get_weather": "Checking the weather",
    "send_message": "Sending a message",
    "call_contact": "Placing a call",
    "set_light": "Changing the lights",
    "set_thermostat": "Changing the thermostat",
}


def step_label(tool: str, args: dict | None) -> str:
    template = STEP_LABELS.get(tool)
    if template is None:
        return tool.replace("_", " ").capitalize()
    with contextlib.suppress(KeyError):
        return template.format(**(args or {}))
    return template.partition(" ")[0]


def _parse(result: Any) -> dict:
    """Tool results arrive as dicts or as JSON text, depending on the path they took."""
    if isinstance(result, dict):
        return result
    with contextlib.suppress(TypeError, ValueError):
        parsed = json.loads(result)
        if isinstance(parsed, dict):
            return parsed
    return {}


def _read_json(path: Path, default: dict) -> dict:
    with contextlib.suppress(OSError, ValueError):
        return json.loads(path.read_text())
    return default


class _FileCache:
    """Re-read a JSON file only when it changes: the state is polled ten times a second."""

    def __init__(self):
        self._seen: dict[Path, tuple[tuple[float, int] | None, dict]] = {}

    def read(self, path: Path, default: dict) -> dict:
        try:
            st = path.stat()
            key = (st.st_mtime, st.st_size)
        except OSError:
            key = None
        cached = self._seen.get(path)
        if cached and cached[0] == key:
            return cached[1]
        value = _read_json(path, default) if key else default
        self._seen[path] = (key, value)
        return value


_files = _FileCache()


def read_devices(data_dir: Path, now: float) -> dict:
    timers = _files.read(data_dir / "clock.json", {})
    pending = sorted((i for i in timers.values() if i["fires_at"] > now), key=lambda i: i["fires_at"])
    return {
        "lights": list(_files.read(data_dir / "lights.json", DEFAULT_LIGHTS).values()),
        "thermostat": _files.read(data_dir / "thermostat.json", DEFAULT_THERMOSTAT),
        "timers": [{"kind": i["kind"], "label": i["label"], "fires_at": i["fires_at"],
                    "created_at": i.get("created_at")} for i in pending[:TIMERS_SHOWN]],  # fmt: skip
        "timers_more": max(0, len(pending) - TIMERS_SHOWN),
    }


class UiState:
    """What the page shows. ``activity`` is what the agent is doing, ``mood`` how that went."""

    def __init__(self, data_dir: Path, clock: Callable[[], float] = time.monotonic):
        self._data_dir = data_dir
        self._clock = clock
        self.activity = "idle"   # idle | listening | thinking | working | asking | speaking
        self.mood = "neutral"    # neutral | happy | sad | angry
        self._mood_until = 0.0
        self.muted = False
        self.step = ""
        self.heard = ""
        self.said = ""
        self.music = {"playing": False, "now_playing": ""}
        self.spotify_player = False
        self.voice_down = False
        self.network_down = False   # set and cleared only by the network events (ADR-037)
        self._new_reply = True
        self._awaiting_answer = False
        self.agent: dict = {"id": "generalist", "name": "Generalist", "color": "#7fd3ee", "kind": "generalist",
                            "model": "direct"}
        self.screen_tools: list[str] = []  # what a tap may run in this session; set by the bot
        self.switching = ""
        self.history: deque[dict] = deque(maxlen=HISTORY_TURNS)
        self.history_version = 0
        self._reply_open = False

    def set_agent(self, agent: Agent, *, announce: bool = False) -> None:
        self.agent = {"id": agent.id, "name": agent.name, "color": agent.color, "kind": agent.kind,
                      "model": agent.model}
        self.switching = ""
        if announce:
            self._add("system", f"Now talking to {agent.name}")

    # Chat history: one entry per user turn and per reply. A reply is committed when it ends,
    # so the history event goes out once a turn, not once a token.

    def _add(self, who: str, text: str) -> None:
        self._end_reply()
        if text.strip():
            self.history.append({"who": who, "text": text.strip()[:HISTORY_CHARS]})
            self.history_version += 1

    def _end_reply(self) -> None:
        if self._reply_open:
            self._reply_open = False
            self.history_version += 1

    def _set_mood(self, mood: str) -> None:
        self.mood = mood
        self._mood_until = self._clock() + MOOD_HOLD_S

    def on_text(self, text: str) -> None:
        """Reply text as it streams from the LLM, for the caption and the tone of the face."""
        if self._new_reply:
            self._new_reply = False
            self.said = ""
            self._end_reply()
            self.history.append({"who": "agent", "text": ""})
            self._reply_open = True
        self.said += text
        if self._reply_open and self.history:
            self.history[-1]["text"] = self.said.strip()[:HISTORY_CHARS]
        if self.said.lstrip().lower().startswith(SORRY_WORDS):
            self._set_mood("sad")

    def _tool_end(self, name: str, result: Any) -> None:
        parsed = _parse(result)
        self.step = ""
        if parsed.get("status") in ("waiting_for_undo", "needs_confirmation"):
            return  # not run yet, so not a success or a failure
        failed = "error" in parsed or parsed.get("status") in ("denied", "not_done")
        self._set_mood("sad" if failed else "happy")
        if "now_playing" in parsed:
            self.music = {"playing": bool(parsed.get("playing", True)), "now_playing": parsed["now_playing"] or ""}
        elif name == "stop_music" and not failed:
            self.music["playing"] = False

    def on_event(self, rec: dict) -> None:
        event = rec["event"]
        if event in ("vad_onset", "user_turn_start", "interruption"):
            self.activity = "listening"
        elif event == "asr_final" or event == "user_text":
            self.heard = rec.get("text", "")
            self._add("user", self.heard)
            self._new_reply = True
            self.step = ""
            self.activity = "thinking"
        elif event == "llm_request":
            if self.activity != "speaking":
                self.activity = "thinking"
        elif event == "tool_call_start":
            self._end_reply()
            self.activity = "working"
            self.step = step_label(rec.get("name", ""), rec.get("args"))
            self._new_reply = True
        elif event == "tool_call_end":
            self._tool_end(rec.get("name", ""), rec.get("result"))
            self.activity = "thinking"
        elif event == "tool_policy":
            decision = rec.get("decision", "")
            if decision in ANGRY_DECISIONS:
                self._set_mood("angry")
            elif decision in ASKING_DECISIONS or decision == "hold_confirm":
                self._awaiting_answer = True
                self.activity = "asking"
                command = (rec.get("args") or {}).get("command")
                if command:  # the read-back is in plain words, so the screen shows the exact text
                    self.step = f"Asking to run: {command}"
            elif decision == "confirmed" or decision.startswith("denied"):
                self._awaiting_answer = False
        elif event == "first_audio_out":
            self.activity = "speaking"
            self.voice_down = False
        elif event == "audio_silent":
            self._end_reply()
            if self.activity == "speaking":
                self.activity = "asking" if self._awaiting_answer else "idle"
        elif event in ("pipeline_error", "spend_cap_reached", "reply_timeout"):
            self._set_mood("sad")
            self.activity = "idle"
        elif event == "tts_failed":
            # The spoken phrases need TTS too, so the screen is the only way left to say so.
            self.voice_down = True
            self.activity = "idle"
        elif event == "network_lost":
            self.network_down = True
            self.activity = "idle"
        elif event == "network_restored":
            self.network_down = False
        elif event == "mic_mute":
            self.muted = bool(rec.get("muted"))
        elif event == "timer_due":
            self.said = rec.get("phrase", "")
            self._add("agent", self.said)
            self._set_mood("happy")

    def face(self) -> str:
        """The expression to draw: one of the mood faces, or a face for the current activity."""
        if self.muted:
            return "muted"
        if self.activity in ("listening", "thinking", "working", "asking"):
            return self.activity
        if self.voice_down or self.network_down:
            return "sad"
        return self.mood if self._clock() < self._mood_until else "neutral"

    def snapshot(self) -> dict:
        return {
            "face": self.face(),
            "activity": self.activity,
            "talking": self.activity == "speaking",
            "muted": self.muted,
            "voice_down": self.voice_down,
            "network_down": self.network_down,
            "step": self.step,
            "heard": self.heard,
            "said": self.said,
            "music": self.music,
            "spotify_player": self.spotify_player,
            "agent": self.agent,
            "switching": self.switching,
            "screen_tools": self.screen_tools,
            "history_version": self.history_version,
            **read_devices(self._data_dir, time.time()),
        }

    def history_snapshot(self) -> list[dict]:
        done = list(self.history)
        if self._reply_open and done:
            done = done[:-1]  # the reply still streaming shows in the caption instead
        return done


class UiServer:
    """Serves the page and pushes state to it. Binds to this machine unless told otherwise."""

    def __init__(self, state: UiState, host: str = "127.0.0.1", port: int = 8770):
        self.state = state
        self.host = host
        self.port = port
        self.on_mute: Callable[[], object] | None = None
        self.spotify_token: Callable[[], str] | None = None   # set only when the player is on
        self.on_player_status: Callable[[str, str], object] | None = None
        # Set by the bot for one session: runs a tapped tool through the tool policy.
        self.on_screen_tool: Callable[[str, dict], Awaitable[dict]] | None = None
        # Set by the bot for one session: ends it so the next one starts with the new agent.
        self.on_switch: Callable[[], Awaitable[None]] | None = None
        self.agents: AgentStore | None = None
        self._clients: set[asyncio.Queue] = set()
        self._runner: web.AppRunner | None = None
        self._pump: asyncio.Task | None = None

    def _app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/", self._page)
        app.router.add_get("/events", self._events)
        app.router.add_post("/api/mute", self._mute)
        app.router.add_get("/spotify-player.js", self._player_script)
        app.router.add_get("/api/spotify-token", self._token)
        app.router.add_post("/api/spotify-status", self._player_status)
        app.router.add_post("/api/spotify-track", self._player_track)
        app.router.add_post("/api/music", self._music)
        app.router.add_post("/api/light", self._light)
        app.router.add_post("/api/thermostat", self._thermostat)
        app.router.add_get("/api/agents", self._agents)
        app.router.add_post("/api/agents/active", self._choose_agent)
        app.router.add_post("/api/agents/save", self._save_agent)
        app.router.add_post("/api/agents/delete", self._delete_agent)
        return app

    async def _page(self, _request: web.Request) -> web.FileResponse:
        return web.FileResponse(PAGE, headers={"Cache-Control": "no-store"})

    async def _events(self, request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-store"})
        await response.prepare(request)
        queue: asyncio.Queue = asyncio.Queue(maxsize=8)
        self._clients.add(queue)
        try:
            await response.write(self._frame(self.state.snapshot()))
            await response.write(self._frame(self.state.history_snapshot(), "history"))
            while (frame := await queue.get()) is not None:  # None: the server is stopping
                await response.write(frame)
        except (ConnectionResetError, asyncio.CancelledError):
            return response
        finally:
            self._clients.discard(queue)

    def _same_site_only(self, request: web.Request) -> None:
        # A page on another site must not be able to reach these from this browser.
        origin = request.headers.get("Origin")
        if origin and urlsplit(origin).netloc != request.host:
            raise web.HTTPForbidden(text="wrong origin")
        if request.host.partition(":")[0] not in LOCAL_HOSTS:  # a rebound domain name lands here
            raise web.HTTPForbidden(text="wrong host")

    async def _player_script(self, _request: web.Request) -> web.FileResponse:
        if self.spotify_token is None:
            raise web.HTTPNotFound()
        return web.FileResponse(PLAYER_SCRIPT, headers={"Cache-Control": "no-store"})

    async def _token(self, request: web.Request) -> web.Response:
        self._same_site_only(request)
        if self.spotify_token is None:
            raise web.HTTPNotFound()
        try:
            token = await asyncio.to_thread(self.spotify_token)
        except MusicError as e:
            raise web.HTTPServiceUnavailable(text=str(e)) from e
        return web.json_response({"token": token}, headers={"Cache-Control": "no-store"})

    async def _player_status(self, request: web.Request) -> web.Response:
        self._same_site_only(request)
        if self.spotify_token is None:
            raise web.HTTPNotFound()
        body = await request.json()
        if self.on_player_status:
            self.on_player_status(str(body.get("state", ""))[:40], str(body.get("detail", ""))[:200])
        return web.json_response({})

    async def _player_track(self, request: web.Request) -> web.Response:
        """What the player itself says it is playing, so track changes show without a tool call."""
        self._same_site_only(request)
        if self.spotify_token is None:
            raise web.HTTPNotFound()
        body = await request.json()
        self.state.music = {"playing": bool(body.get("playing")), "now_playing": str(body.get("now_playing", ""))[:200]}
        return web.json_response({})

    async def _tap(self, tool: str, args: dict) -> web.Response:
        """Run a tapped tool through the tool policy, as if asked by voice."""
        if self.on_screen_tool is None:
            raise web.HTTPConflict(text="no session is running")
        result = await self.on_screen_tool(tool, args)
        self.state._tool_end(tool, result)
        return web.json_response({"result": result})

    async def _body(self, request: web.Request) -> dict:
        self._same_site_only(request)
        with contextlib.suppress(ValueError):
            body = await request.json()
            if isinstance(body, dict):
                return body
        raise web.HTTPBadRequest(text="expected a JSON object")

    async def _music(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        tool = MUSIC_BUTTONS.get(str(body.get("action")))
        if tool is None:
            raise web.HTTPBadRequest(text="unknown action")
        return await self._tap(tool, {})

    async def _light(self, request: web.Request) -> web.Response:
        """A tap on a light: the page sends the room and whether it should be on."""
        body = await self._body(request)
        return await self._tap("set_light", {"room": body.get("room"), "is_on": body.get("is_on")})

    async def _thermostat(self, request: web.Request) -> web.Response:
        """A step button: the page sends the new setpoint, and the policy checks its range and step."""
        body = await self._body(request)
        return await self._tap("set_thermostat", {"target_c": body.get("target_c")})

    def _store(self) -> AgentStore:
        if self.agents is None:
            raise web.HTTPNotFound(text="agents are off for this run")
        return self.agents

    async def _agents(self, request: web.Request) -> web.Response:
        self._same_site_only(request)
        store = self._store()
        return web.json_response({
            "agents": [a.public() for a in store.all().values()],
            "active": self.state.agent["id"],
            "picks": store.picks(),
            "colors": store.colors,
            "models": store.models(),
            "documents": await asyncio.to_thread(store.document_choices),
        }, headers={"Cache-Control": "no-store"})  # fmt: skip

    async def _choose_agent(self, request: web.Request) -> web.Response:
        self._same_site_only(request)
        body = await request.json()
        try:
            agent = self._store().set_active(str(body.get("id", "")), str(body.get("model") or "") or None)
        except AgentError as e:
            raise web.HTTPBadRequest(text=str(e)) from e
        if (agent.id, agent.model) != (self.state.agent["id"], self.state.agent.get("model")):
            if self.on_switch:
                self.state.switching = agent.name
                await self.on_switch()
            else:  # no session to restart (the demo): show the pick at once
                self.state.set_agent(agent, announce=True)
        return web.json_response({"active": agent.id})

    async def _save_agent(self, request: web.Request) -> web.Response:
        self._same_site_only(request)
        body = await request.json()
        try:
            agent = self._store().save(body if isinstance(body, dict) else {})
        except AgentError as e:
            raise web.HTTPBadRequest(text=str(e)) from e
        return web.json_response({"agent": agent.public()})

    async def _delete_agent(self, request: web.Request) -> web.Response:
        self._same_site_only(request)
        body = await request.json()
        agent_id = str(body.get("id", ""))
        if agent_id == self.state.agent["id"]:
            raise web.HTTPBadRequest(text="switch to another agent before deleting this one")
        try:
            self._store().delete(agent_id)
        except AgentError as e:
            raise web.HTTPBadRequest(text=str(e)) from e
        return web.json_response({})

    async def _mute(self, request: web.Request) -> web.Response:
        self._same_site_only(request)
        if self.on_mute is None:
            raise web.HTTPConflict(text="this run has no mic")
        self.on_mute()
        return web.json_response({"muted": self.state.muted})

    @staticmethod
    def _frame(data: dict | list, event: str = "") -> bytes:
        head = f"event: {event}\n" if event else ""
        return f"{head}data: {json.dumps(data)}\n\n".encode()

    def _broadcast(self, frame: bytes) -> None:
        for queue in self._clients:
            if queue.full():  # a stalled page catches up on the next change
                queue.get_nowait()
            queue.put_nowait(frame)

    async def _send_changes(self, every_s: float = 0.1) -> None:
        last = None
        last_history = self.state.history_version
        while True:
            if self._clients:  # nothing to build while no page is open
                snapshot = self.state.snapshot()
                if snapshot["history_version"] != last_history:
                    last_history = snapshot["history_version"]
                    self._broadcast(self._frame(self.state.history_snapshot(), "history"))
                if snapshot != last:
                    last = snapshot
                    self._broadcast(self._frame(snapshot))
            await asyncio.sleep(every_s)

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app())
        await self._runner.setup()
        await web.TCPSite(self._runner, self.host, self.port).start()
        self._pump = asyncio.create_task(self._send_changes())

    async def stop(self) -> None:
        # Wake every open page's stream so cleanup does not wait out its 60 s shutdown timeout.
        for queue in self._clients:
            while queue.full():
                queue.get_nowait()
            queue.put_nowait(None)
        if self._pump:
            self._pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pump
        if self._runner:
            await self._runner.cleanup()


DEMO_SCRIPT: list[tuple[float, str, dict]] = [
    (2.5, "vad_onset", {}),
    (2.0, "asr_final", {"text": "Build me a website about my robot."}),
    (2.0, "tool_call_start", {"name": "write_file", "args": {"path": "robot/index.html"}}),
    (2.5, "tool_call_end", {"name": "write_file", "result": {"path": "robot/index.html", "bytes": 2048}}),
    (0.2, "first_audio_out", {}),
    (4.0, "audio_silent", {}),
    (4.0, "tool_call_start", {"name": "bash", "args": {"command": "rm -rf /"}}),
    (0.2, "tool_policy", {"decision": "denied_guard"}),
    (4.0, "tool_call_end", {"name": "bash", "result": {"status": "denied"}}),
    (4.0, "tool_policy", {"decision": "read_back"}),
    (4.0, "audio_silent", {}),
    (4.0, "pipeline_error", {"error": "demo"}),
    (4.0, "tts_failed", {"error": "demo"}),
    (3.0, "network_lost", {"stage": "llm", "host": "demo"}),
    (5.0, "network_restored", {"stages": ["llm"], "down_ms": 5000}),
    (5.0, "first_audio_out", {}),
    (2.0, "audio_silent", {}),
]


async def _demo(state: UiState, server: UiServer) -> None:
    def toggle() -> None:
        state.on_event({"event": "mic_mute", "muted": not state.muted})

    data = state._data_dir

    async def tap(tool: str, args: dict) -> dict:
        # The demo has no tool servers, so it writes the mock state files the way they do.
        if tool == "set_light":
            lights = _read_json(data / "lights.json", DEFAULT_LIGHTS)
            lights[args["room"]]["is_on"] = args["is_on"]
            (data / "lights.json").write_text(json.dumps(lights))
            return lights[args["room"]]
        if tool == "set_thermostat":
            thermo = _read_json(data / "thermostat.json", DEFAULT_THERMOSTAT) | {"target_c": args["target_c"]}
            (data / "thermostat.json").write_text(json.dumps(thermo))
            return thermo
        playing = tool != "stop_music"
        return {"playing": playing, "now_playing": "Demo Song by The Fakes" if playing else state.music["now_playing"]}

    server.on_mute = toggle
    server.on_screen_tool = tap
    state.screen_tools = ["next_track", "resume_music", "set_light", "set_thermostat", "stop_music"]
    while True:
        for wait, event, fields in DEMO_SCRIPT:
            await asyncio.sleep(wait)
            state.on_event({"event": event, **fields})
            if event == "first_audio_out":
                state.on_text("All done. I wrote your robot a home page.")


async def _serve_demo(host: str, port: int, data_dir: Path) -> None:
    state = UiState(data_dir)
    server = UiServer(state, host, port)
    server.agents = AgentStore(data_dir, load_config("mac").providers)
    state.set_agent(server.agents.active())
    await server.start()
    print(f"open http://{host}:{port}")
    try:
        await _demo(state, server)
    finally:
        await server.stop()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true", required=True, help="cycle the expressions with no bot")
    ap.add_argument("--profile", choices=["mac", "pi"], default="mac")
    ap.add_argument("--data-dir", type=Path, help="read devices and agents from here instead of data/")
    ap.add_argument("--port", type=int, help="override the profile's port")
    args = ap.parse_args()
    cfg = load_config(args.profile).profile
    data = args.data_dir or REPO_ROOT / cfg["paths"]["data_dir"]
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_serve_demo(cfg["ui"]["host"], args.port or cfg["ui"]["port"], data))


if __name__ == "__main__":
    main()
