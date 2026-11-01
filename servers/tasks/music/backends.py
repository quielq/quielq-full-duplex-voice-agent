"""Music backends behind the `music` tools: a mock, and Spotify for demos (ADR-022).

A failure the user should hear about raises ``MusicError``. Its text is safe to speak and
holds no response body, because responses can carry tokens.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import httpx
import yaml
from dotenv import set_key

from bench.cost_ledger import CostLedger, SpendRefused
from servers.common import REPO_ROOT, data_dir

API_URL = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"
TIMEOUT_S = 2.5
QUEUE_SIZE = 10  # Spotify's Development Mode search maximum
TOKEN_MARGIN_S = 30
START_CHECK_S = 3.0  # with the requests before it, this stays inside the tool's 10 s policy timeout
START_POLL_S = 0.4


class MusicError(RuntimeError):
    """The action did not happen. The message is short enough to read aloud."""


class MockBackend:
    """A player state in $ME3_DATA_DIR/music.json, and no audio plays."""

    DEFAULT = {"playing": False, "query": "", "now_playing": "", "volume": 50, "mock": True,
               "note": "This is a mock player and no audio plays. Tell the user so."}  # fmt: skip

    def _load(self) -> dict:
        p = data_dir() / "music.json"
        return self.DEFAULT | json.loads(p.read_text()) if p.exists() else dict(self.DEFAULT)

    def _save(self, state: dict) -> dict:
        (data_dir() / "music.json").write_text(json.dumps(state))
        return state

    def now_playing(self) -> dict:
        return self._load()

    def play(self, query: str) -> dict:
        return self._save(self._load() | {"playing": True, "query": query, "now_playing": query})

    def stop(self) -> dict:
        return self._save(self._load() | {"playing": False})

    def resume(self) -> dict:
        return self._continue()

    def next(self) -> dict:
        return self._continue()

    def previous(self) -> dict:
        return self._continue()

    def _continue(self) -> dict:
        state = self._load()
        if not state["now_playing"]:
            raise MusicError("Nothing has been played yet.")
        return self._save(state | {"playing": True})

    def set_volume(self, level: int) -> dict:
        return self._save(self._load() | {"volume": level})


def _ledger() -> CostLedger:
    cfg = yaml.safe_load((REPO_ROOT / "configs" / "providers.yaml").read_text())
    return CostLedger.from_providers_config(cfg, data_dir() / "cost_ledger.jsonl")


def _title(track: dict) -> str:
    artists = track.get("artists") or []
    return f"{track['name']} by {artists[0]['name']}" if artists else track["name"]


def pick_device(devices: list[dict]) -> dict:
    """The active device, then the computer, so a smart speaker never starts by surprise."""
    usable = [d for d in devices if not d.get("is_restricted")]
    for wanted in (lambda d: d.get("is_active"), lambda d: d.get("type") == "Computer", lambda d: True):
        for device in usable:
            if wanted(device):
                return device
    raise MusicError("No Spotify device is open. Open the Spotify app and try again.")


def play_in_mac_app(uri: str, context: str) -> None:
    """Start a track inside its album in this Mac's Spotify app. Both URIs are arguments, never script text."""
    tell = 'tell application "Spotify" to play track (item 1 of argv) in context (item 2 of argv)'
    args = [part for line in ("on run argv", tell, "end run") for part in ("-e", line)]
    try:
        subprocess.run(["osascript", *args, uri, context], capture_output=True, timeout=TIMEOUT_S, check=True)
    except subprocess.TimeoutExpired:
        pass  # a slow app can still start, so the start check decides
    except (OSError, subprocess.SubprocessError) as e:
        raise MusicError("I could not start the Spotify app on this Mac.") from e


def _track_uris(state: dict) -> set[str | None]:
    item = state.get("item") or {}
    # Spotify can swap a track for a regional copy, and then linked_from has the asked-for URI.
    return {item.get("uri"), (item.get("linked_from") or {}).get("uri")} - {None}


def _is_playing(state: dict, uris: list[str]) -> bool:
    return bool(state.get("is_playing")) and not _track_uris(state).isdisjoint(uris)


def _summary(state: dict) -> dict:
    device = state.get("device") or {}
    item = state.get("item")
    return {
        "playing": bool(state.get("is_playing")),
        "now_playing": _title(item) if item else "",
        "volume": device.get("volume_percent"),
        "device": device.get("name", ""),
    }


class SpotifyBackend:
    """Controls a Spotify app over the Web API. The audio stays in that app.

    `local_play` starts a track on this computer, because the Web API accepts a play for the
    Mac app and leaves it stopped (ADR-022). Nothing counts as playing until the player says so.
    """

    def __init__(
        self,
        client: httpx.Client | None = None,
        ledger: CostLedger | None = None,
        local_play: Callable[[str], None] | None = None,
    ):
        self._client = client or httpx.Client(timeout=TIMEOUT_S)
        self._ledger = ledger or _ledger()
        self._local_play = local_play
        self._token = ""
        self._expires = 0.0

    def _refresh(self) -> None:
        refresh_token = os.environ.get("SPOTIFY_REFRESH_TOKEN", "").strip()
        client_id = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
        secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
        if not (refresh_token and client_id and secret):
            raise MusicError("Spotify is not set up. Run the Spotify login first.")
        r = self._client.post(
            TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            auth=(client_id, secret),
        )
        if r.status_code != 200:
            raise MusicError("Spotify refused the login. Run the Spotify login again.")
        body = r.json()
        self._token = body["access_token"]
        self._expires = time.monotonic() + body.get("expires_in", 3600) - TOKEN_MARGIN_S
        rotated = body.get("refresh_token")
        if rotated and rotated != refresh_token:
            os.environ["SPOTIFY_REFRESH_TOKEN"] = rotated
            env_path = Path(REPO_ROOT / ".env")
            if env_path.exists():
                set_key(str(env_path), "SPOTIFY_REFRESH_TOKEN", rotated)

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """One API call through the ledger, with a fresh token and one retry on a 401."""
        try:
            self._ledger.authorize("spotify")
        except SpendRefused as e:
            raise MusicError("Spotify calls are switched off for today.") from e
        try:
            if time.monotonic() >= self._expires:
                self._refresh()
            r = self._send(method, path, **kwargs)
            if r.status_code == 401:
                self._refresh()
                r = self._send(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise MusicError("Spotify did not answer in time.") from e
        self._ledger.record("spotify", "spotify_requests", 1, 0.0)
        if r.status_code == 429:
            raise MusicError("Spotify is rate limiting us. Try again in a minute.")
        return r

    def _send(self, method: str, path: str, **kwargs) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._token}"}
        return self._client.request(method, f"{API_URL}{path}", headers=headers, **kwargs)

    def _checked(self, method: str, path: str, **kwargs) -> httpx.Response:
        r = self._request(method, path, **kwargs)
        if r.status_code >= 300:
            raise MusicError(f"Spotify refused that, with status {r.status_code}.")
        return r

    def _player(self) -> dict:
        r = self._checked("GET", "/me/player")
        return r.json() if r.status_code == 200 and r.content else {}

    def _loaded_player(self) -> dict:
        state = self._player()
        if not state.get("item"):
            raise MusicError("Nothing has been played yet.")
        return state

    def now_playing(self) -> dict:
        state = self._player()
        return _summary(state) if state else {"playing": False, "now_playing": "", "volume": None, "device": ""}

    def play(self, query: str) -> dict:
        found = self._checked("GET", "/search", params={"q": query, "type": "track", "limit": QUEUE_SIZE})
        tracks = found.json().get("tracks", {}).get("items", [])
        tracks = [t for t in tracks if t]
        if not tracks:
            raise MusicError(f"Spotify found nothing for {query}.")
        device = pick_device(self._checked("GET", "/me/player/devices").json().get("devices", []))
        uris = [t["uri"] for t in tracks]
        if self._local_play and device.get("type") == "Computer":
            uris = uris[:1]  # the album supplies what comes next
            self._local_play(uris[0], tracks[0]["album"]["uri"])
        else:
            self._checked("PUT", "/me/player/play", params={"device_id": device["id"]}, json={"uris": uris})
        self._confirm_started(uris)
        return {
            "playing": True,
            "query": query,
            "now_playing": _title(tracks[0]),
            "volume": device.get("volume_percent"),
            "device": device.get("name", ""),
        }

    def _wait_for(self, ready: Callable[[dict], bool], failure: str) -> dict:
        deadline = time.monotonic() + START_CHECK_S
        while True:
            state = self._player()
            if ready(state):
                return state
            if time.monotonic() >= deadline:
                raise MusicError(failure)
            time.sleep(START_POLL_S)

    def _confirm_started(self, uris: list[str]) -> None:
        self._wait_for(lambda s: _is_playing(s, uris), "Spotify accepted that, but nothing started playing.")

    def resume(self) -> dict:
        state = self._loaded_player()
        if not state.get("is_playing"):  # Spotify can refuse a play with no body while already playing
            uris = list(_track_uris(state))
            self._checked("PUT", "/me/player/play")
            state = self._wait_for(lambda s: _is_playing(s, uris), "Spotify did not resume the music.")
        return _summary(state)

    def next(self) -> dict:
        return self._skip("/me/player/next")

    def previous(self) -> dict:
        return self._skip("/me/player/previous")

    def _skip(self, path: str) -> dict:
        before = self._loaded_player()
        uris, position = _track_uris(before), before.get("progress_ms", 0)

        def moved(state: dict) -> bool:  # previous can restart the same track, so position counts
            return bool(state.get("item")) and (_track_uris(state) != uris or state.get("progress_ms", 0) < position)

        self._checked("POST", path)
        return _summary(self._wait_for(moved, "Spotify did not change the track."))

    def stop(self) -> dict:
        r = self._request("PUT", "/me/player/pause")
        # 403 and 404 mean nothing is playing, which is what was asked for.
        if r.status_code >= 300 and r.status_code not in (403, 404):
            raise MusicError(f"Spotify refused that, with status {r.status_code}.")
        return {"playing": False}

    def set_volume(self, level: int) -> dict:
        r = self._request("PUT", "/me/player/volume", params={"volume_percent": level})
        if r.status_code == 404:
            raise MusicError("Nothing is playing on Spotify, so there is no volume to set.")
        if r.status_code == 403:
            raise MusicError("This Spotify device does not allow volume control.")
        if r.status_code >= 300:
            raise MusicError(f"Spotify refused that, with status {r.status_code}.")
        return {"volume": level}


def build_backend(choice: str):
    if choice == "fake":
        return MockBackend()
    if choice == "spotify":
        return SpotifyBackend(local_play=play_in_mac_app if sys.platform == "darwin" else None)
    raise ValueError(f"unknown music backend {choice!r}")
