"""The music backends on a mock transport. No network, and no real Spotify."""

from __future__ import annotations

import json
import os

import httpx
import pytest
from fastmcp import Client

from bench.cost_ledger import CostLedger
from harness.mcp_tools import _server_env
from servers.tasks.music import backends, server
from servers.tasks.music.backends import MusicError, SpotifyBackend, build_backend, pick_device
from servers.tasks.music.server import mcp

MAC = {"id": "mac", "name": "Mac", "type": "Computer", "is_active": False, "volume_percent": 40}
SPEAKER = {"id": "spk", "name": "Speaker", "type": "Speaker", "is_active": False}
ALBUM = {"uri": "spotify:album:1"}
TRACK = {"name": "Valentine", "uri": "spotify:track:1", "artists": [{"name": "Laufey"}], "album": ALBUM}
OTHER = TRACK | {"name": "Bewitched", "uri": "spotify:track:2"}
PLAYING = {"is_playing": True, "item": TRACK}
PAUSED = {"is_playing": False, "item": TRACK, "progress_ms": 40000}


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("ME3_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "sec")
    monkeypatch.setenv("SPOTIFY_REFRESH_TOKEN", "rt")
    monkeypatch.setattr(backends, "START_CHECK_S", 0.02)
    monkeypatch.setattr(backends, "START_POLL_S", 0.005)
    server._backend.cache_clear()
    yield
    server._backend.cache_clear()


class FakeSpotify:
    """Answers like the API did in the Nov 1 spike. Set `replies` to change one route."""

    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.replies: dict[str, httpx.Response | list[httpx.Response] | Exception] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        route = request.url.path.removeprefix("/v1")
        if route == "/api/token":
            default = httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        elif route == "/search":
            default = httpx.Response(200, json={"tracks": {"items": [TRACK]}})
        elif route == "/me/player/devices":
            default = httpx.Response(200, json={"devices": [SPEAKER, MAC]})
        else:
            default = httpx.Response(204)
        reply = self.replies.get(route, default)
        if isinstance(reply, list):
            reply = reply.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def routes(self) -> list[str]:
        return [c.url.path.removeprefix("/v1") for c in self.calls]


@pytest.fixture
def fake():
    return FakeSpotify()


@pytest.fixture
def backend(fake, tmp_path):
    ledger = CostLedger(tmp_path / "ledger.jsonl", {"spotify": 0.01})
    return SpotifyBackend(httpx.Client(transport=httpx.MockTransport(fake)), ledger)


def test_play_queues_results_on_the_computer_and_names_the_track(fake, backend):
    fake.replies["/search"] = httpx.Response(200, json={"tracks": {"items": [TRACK, None, TRACK]}})
    fake.replies["/me/player"] = httpx.Response(200, json=PLAYING)
    result = backend.play("jazz")
    assert result["now_playing"] == "Valentine by Laufey" and result["playing"] is True
    play = next(c for c in fake.calls if c.url.path.endswith("/me/player/play"))
    assert play.url.params["device_id"] == "mac"
    assert len(json.loads(play.content)["uris"]) == 2


def test_an_accepted_play_that_never_starts_is_an_error(fake, backend):
    """The Nov 1 failure: play returned 204 and the Mac app stayed stopped."""
    fake.replies["/me/player"] = httpx.Response(200, json={"is_playing": False, "item": None})
    with pytest.raises(MusicError, match="nothing started"):
        backend.play("jazz")


def test_no_player_session_after_a_play_is_an_error(fake, backend):
    with pytest.raises(MusicError, match="nothing started"):
        backend.play("jazz")


def test_the_old_track_still_playing_is_an_error(fake, backend):
    other = TRACK | {"uri": "spotify:track:old"}
    fake.replies["/me/player"] = httpx.Response(200, json={"is_playing": True, "item": other})
    with pytest.raises(MusicError, match="nothing started"):
        backend.play("jazz")


def test_a_slow_start_is_still_a_success(fake, backend):
    fake.replies["/me/player"] = [httpx.Response(204), httpx.Response(200, json=PLAYING)]
    assert backend.play("jazz")["playing"] is True


def test_a_relinked_track_counts_as_started(fake, backend):
    item = TRACK | {"uri": "spotify:track:regional", "linked_from": {"uri": TRACK["uri"]}}
    fake.replies["/me/player"] = httpx.Response(200, json={"is_playing": True, "item": item})
    assert backend.play("jazz")["playing"] is True


def _local_backend(fake, tmp_path, started: list[tuple[str, str]]) -> SpotifyBackend:
    ledger = CostLedger(tmp_path / "ledger.jsonl", {"spotify": 0.01})
    client = httpx.Client(transport=httpx.MockTransport(fake))
    return SpotifyBackend(client, ledger, local_play=lambda uri, context: started.append((uri, context)))


def test_a_computer_plays_through_the_local_app_not_the_web_api(fake, tmp_path):
    fake.replies["/me/player"] = httpx.Response(200, json=PLAYING)
    started: list[tuple[str, str]] = []
    assert _local_backend(fake, tmp_path, started).play("jazz")["playing"] is True
    assert started == [(TRACK["uri"], ALBUM["uri"])]
    assert "/me/player/play" not in fake.routes()


def test_an_active_speaker_still_uses_the_web_api(fake, tmp_path):
    active = SPEAKER | {"is_active": True}
    fake.replies["/me/player/devices"] = httpx.Response(200, json={"devices": [MAC, active]})
    fake.replies["/me/player"] = httpx.Response(200, json=PLAYING)
    started: list[tuple[str, str]] = []
    _local_backend(fake, tmp_path, started).play("jazz")
    assert started == [] and "/me/player/play" in fake.routes()


def test_the_local_app_is_checked_too(fake, tmp_path):
    with pytest.raises(MusicError, match="nothing started"):
        _local_backend(fake, tmp_path, []).play("jazz")


def test_the_uris_reach_osascript_as_arguments(monkeypatch):
    seen = {}
    monkeypatch.setattr(backends.subprocess, "run", lambda cmd, **kw: seen.update(cmd=cmd))
    backends.play_in_mac_app('spotify:track:1" & quit', "spotify:album:1")
    assert seen["cmd"][-2:] == ['spotify:track:1" & quit', "spotify:album:1"]
    assert not any("track:1" in part or "album:1" in part for part in seen["cmd"][:-2])


def test_a_slow_osascript_leaves_the_decision_to_the_start_check(monkeypatch):
    """Nov 1: the call passed its time limit while the app did start the song."""

    def slow(cmd, **kw):
        raise backends.subprocess.TimeoutExpired(cmd, kw["timeout"])

    monkeypatch.setattr(backends.subprocess, "run", slow)
    backends.play_in_mac_app("spotify:track:1", "spotify:album:1")


def test_resume_continues_without_a_new_search_or_track(fake, backend):
    fake.replies["/me/player"] = [httpx.Response(200, json=PAUSED), httpx.Response(200, json=PLAYING)]
    assert backend.resume()["now_playing"] == "Valentine by Laufey"
    assert "/search" not in fake.routes()
    play = next(c for c in fake.calls if c.url.path.endswith("/me/player/play"))
    assert play.content == b""


def test_resume_while_playing_sends_no_play(fake, backend):
    fake.replies["/me/player"] = httpx.Response(200, json=PLAYING)
    assert backend.resume()["playing"] is True
    assert "/me/player/play" not in fake.routes()


def test_resume_that_never_starts_is_an_error(fake, backend):
    fake.replies["/me/player"] = httpx.Response(200, json=PAUSED)
    with pytest.raises(MusicError, match="did not resume"):
        backend.resume()


@pytest.mark.parametrize("action", ["resume", "next", "previous"])
def test_with_nothing_loaded_the_controls_are_errors(fake, backend, action):
    with pytest.raises(MusicError, match="Nothing has been played"):
        getattr(backend, action)()


@pytest.mark.parametrize(("action", "route"), [("next", "/me/player/next"), ("previous", "/me/player/previous")])
def test_skipping_names_the_new_track(fake, backend, action, route):
    after = {"is_playing": True, "item": OTHER, "progress_ms": 100}
    fake.replies["/me/player"] = [httpx.Response(200, json=PLAYING), httpx.Response(200, json=after)]
    assert getattr(backend, action)()["now_playing"] == "Bewitched by Laufey"
    assert route in fake.routes()


def test_previous_that_restarts_the_track_is_a_success(fake, backend):
    restarted = PAUSED | {"is_playing": True, "progress_ms": 50}
    fake.replies["/me/player"] = [httpx.Response(200, json=PAUSED), httpx.Response(200, json=restarted)]
    assert backend.previous()["now_playing"] == "Valentine by Laufey"


def test_a_skip_that_changes_nothing_is_an_error(fake, backend):
    fake.replies["/me/player"] = httpx.Response(200, json=PLAYING | {"progress_ms": 1000})
    with pytest.raises(MusicError, match="did not change"):
        backend.next()


def test_restart_seeks_to_zero_and_keeps_the_song(fake, backend):
    fake.replies["/me/player"] = [httpx.Response(200, json=PLAYING | {"progress_ms": 60000}),
                                  httpx.Response(200, json=PLAYING | {"progress_ms": 50}),
                                  httpx.Response(200, json=PLAYING | {"progress_ms": 60})]  # fmt: skip
    assert backend.restart()["now_playing"] == "Valentine by Laufey"
    seek = next(c for c in fake.calls if c.url.path.endswith("/seek"))
    assert seek.method == "PUT" and seek.url.params["position_ms"] == "0"
    assert "/search" not in fake.routes()


def test_restart_plays_a_paused_song(fake, backend):
    fake.replies["/me/player"] = [httpx.Response(200, json=PAUSED),
                                  httpx.Response(200, json=PAUSED | {"progress_ms": 0}),
                                  httpx.Response(200, json=PAUSED | {"progress_ms": 0}),
                                  httpx.Response(200, json=PLAYING)]  # fmt: skip
    assert backend.restart()["playing"] is True
    assert "/me/player/play" in fake.routes()


def test_a_restart_that_does_not_move_is_an_error(fake, backend):
    fake.replies["/me/player"] = httpx.Response(200, json=PLAYING | {"progress_ms": 60000})
    with pytest.raises(MusicError, match="did not restart"):
        backend.restart()


async def test_mock_pauses_resumes_and_skips():
    async with Client(mcp) as c:
        assert (await c.call_tool("resume_music", {})).structured_content == {"error": "Nothing has been played yet."}
        await c.call_tool("play_music", {"query": "jazz"})
        await c.call_tool("stop_music", {})
        for tool in ("resume_music", "next_track", "previous_track", "restart_track"):
            r = (await c.call_tool(tool, {})).structured_content
            assert r["playing"] is True and r["now_playing"] == "jazz"


def test_a_failed_osascript_is_a_spoken_error(monkeypatch):
    def fail(cmd, **kw):
        raise backends.subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(backends.subprocess, "run", fail)
    with pytest.raises(MusicError, match="Spotify app"):
        backends.play_in_mac_app("spotify:track:1", "spotify:album:1")


@pytest.mark.parametrize(("platform", "local"), [("darwin", True), ("linux", False)])
def test_only_a_mac_gets_local_playback(monkeypatch, platform, local):
    monkeypatch.setattr(backends.sys, "platform", platform)
    assert (build_backend("spotify")._local_play is not None) is local


def test_device_order_prefers_active_then_computer():
    active = SPEAKER | {"is_active": True}
    assert pick_device([MAC, active])["id"] == "spk"
    assert pick_device([SPEAKER, MAC])["id"] == "mac"
    assert pick_device([SPEAKER])["id"] == "spk"


@pytest.mark.parametrize("devices", [[], [MAC | {"is_restricted": True}]])
def test_no_usable_device_is_an_error_and_nothing_starts(fake, backend, devices):
    fake.replies["/me/player/devices"] = httpx.Response(200, json={"devices": devices})
    with pytest.raises(MusicError, match="device"):
        backend.play("jazz")
    assert "/me/player/play" not in fake.routes()


def test_nothing_found_is_an_error(fake, backend):
    fake.replies["/search"] = httpx.Response(200, json={"tracks": {"items": []}})
    with pytest.raises(MusicError, match="nothing"):
        backend.play("zzz")


def test_expired_token_refreshes_once_and_retries(fake, backend):
    fake.replies["/me/player/volume"] = [httpx.Response(401), httpx.Response(204)]
    assert backend.set_volume(30) == {"volume": 30}
    assert fake.routes().count("/api/token") == 2


def test_rejected_login_tells_the_user_to_log_in_again(fake, backend):
    fake.replies["/api/token"] = httpx.Response(400, json={"error": "secret-looking-body"})
    with pytest.raises(MusicError, match="login") as e:
        backend.stop()
    assert "secret-looking-body" not in str(e.value)


@pytest.mark.parametrize(
    ("status", "text"),
    [(403, "does not allow"), (404, "Nothing is playing"), (429, "rate limiting"), (500, "status 500")],
)
def test_volume_failures_are_spoken_errors(fake, backend, status, text):
    fake.replies["/me/player/volume"] = httpx.Response(status)
    with pytest.raises(MusicError, match=text):
        backend.set_volume(30)


def test_timeout_is_an_error(fake, backend):
    fake.replies["/search"] = httpx.ReadTimeout("slow")
    with pytest.raises(MusicError, match="in time"):
        backend.play("jazz")


@pytest.mark.parametrize("status", [200, 204, 403, 404])
def test_stop_when_already_stopped_succeeds(fake, backend, status):
    fake.replies["/me/player/pause"] = httpx.Response(status)
    assert backend.stop() == {"playing": False}


def test_now_playing_with_no_session(fake, backend):
    assert backend.now_playing()["playing"] is False


def test_now_playing_reads_the_player(fake, backend):
    state = {"is_playing": True, "item": TRACK, "device": {"name": "Mac", "volume_percent": 40}}
    fake.replies["/me/player"] = httpx.Response(200, json=state)
    assert backend.now_playing() == {
        "playing": True, "now_playing": "Valentine by Laufey", "volume": 40, "device": "Mac",
    }  # fmt: skip


def test_zero_cap_refuses_before_any_request(fake, tmp_path):
    ledger = CostLedger(tmp_path / "ledger.jsonl", {"spotify": 0})
    backend = SpotifyBackend(httpx.Client(transport=httpx.MockTransport(fake)), ledger)
    with pytest.raises(MusicError, match="switched off"):
        backend.play("jazz")
    assert fake.calls == []


def test_every_request_is_recorded_at_no_cost(fake, backend, tmp_path):
    backend.set_volume(30)
    lines = (tmp_path / "ledger.jsonl").read_text().splitlines()
    assert [json.loads(x)["usd"] for x in lines] == [0.0]


def test_rotated_refresh_token_replaces_the_old_one_in_memory(fake, backend, monkeypatch):
    monkeypatch.setattr("servers.tasks.music.backends.REPO_ROOT", backend._ledger._path.parent)
    fake.replies["/api/token"] = httpx.Response(
        200, json={"access_token": "at", "expires_in": 3600, "refresh_token": "new"}
    )
    backend.set_volume(30)
    assert os.environ["SPOTIFY_REFRESH_TOKEN"] == "new"


async def test_mock_is_the_default_and_keeps_the_query_for_the_reply():
    async with Client(mcp) as c:
        r = (await c.call_tool("play_music", {"query": "jazz"})).structured_content
        assert r["playing"] is True and r["now_playing"] == "jazz"
        assert (await c.call_tool("stop_music", {})).structured_content["playing"] is False


async def test_a_failed_action_reaches_the_harness_as_an_error(monkeypatch):
    class Broken:
        def play(self, query):
            raise MusicError("No Spotify device is open.")

    monkeypatch.setattr(server, "build_backend", lambda _: Broken())
    async with Client(mcp) as c:
        r = (await c.call_tool("play_music", {"query": "jazz"})).structured_content
    assert r == {"error": "No Spotify device is open."}


async def test_every_tool_is_annotated_as_open_world():
    async with Client(mcp) as c:
        for tool in await c.list_tools():
            assert tool.annotations.openWorldHint is True
            assert tool.annotations.destructiveHint is False


def test_only_the_music_server_receives_spotify_credentials():
    assert "SPOTIFY_CLIENT_SECRET" in _server_env(None, "servers.tasks.music.server")
    for module in ("", "servers.tasks.clock.server"):
        assert not [k for k in _server_env(None, module) if k.startswith("SPOTIFY_")]
