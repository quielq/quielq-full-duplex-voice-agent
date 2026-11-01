"""Music backends behind the `music` tools: a mock, and Spotify for demos (ADR-022).

A failure the user should hear about raises ``MusicError``. Its text is safe to speak and
holds no response body, because responses can carry tokens.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx
import yaml
from dotenv import set_key

from bench.cost_ledger import CostLedger, SpendRefused
from servers.common import REPO_ROOT, data_dir

API_URL = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"
TIMEOUT_S = 2.5  # a call makes up to four requests, inside the tool's 10 s policy timeout
QUEUE_SIZE = 10  # Spotify's Development Mode search maximum
TOKEN_MARGIN_S = 30


class MusicError(RuntimeError):
    """The action did not happen. The message is short enough to read aloud."""


class MockBackend:
    """A player state in $ME3_DATA_DIR/music.json, and no audio plays."""

    DEFAULT = {"playing": False, "query": "", "now_playing": "", "volume": 50}

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


class SpotifyBackend:
    """Controls a Spotify app over the Web API. The audio stays in that app."""

    def __init__(self, client: httpx.Client | None = None, ledger: CostLedger | None = None):
        self._client = client or httpx.Client(timeout=TIMEOUT_S)
        self._ledger = ledger or _ledger()
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

    def now_playing(self) -> dict:
        r = self._checked("GET", "/me/player")
        if r.status_code == 204 or not r.content:
            return {"playing": False, "now_playing": "", "volume": None, "device": ""}
        state = r.json()
        device = state.get("device") or {}
        item = state.get("item")
        return {
            "playing": bool(state.get("is_playing")),
            "now_playing": _title(item) if item else "",
            "volume": device.get("volume_percent"),
            "device": device.get("name", ""),
        }

    def play(self, query: str) -> dict:
        found = self._checked("GET", "/search", params={"q": query, "type": "track", "limit": QUEUE_SIZE})
        tracks = found.json().get("tracks", {}).get("items", [])
        tracks = [t for t in tracks if t]
        if not tracks:
            raise MusicError(f"Spotify found nothing for {query}.")
        device = pick_device(self._checked("GET", "/me/player/devices").json().get("devices", []))
        self._checked(
            "PUT",
            "/me/player/play",
            params={"device_id": device["id"]},
            json={"uris": [t["uri"] for t in tracks]},
        )
        return {
            "playing": True,
            "query": query,
            "now_playing": _title(tracks[0]),
            "volume": device.get("volume_percent"),
            "device": device.get("name", ""),
        }

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
        return SpotifyBackend()
    raise ValueError(f"unknown music backend {choice!r}")
