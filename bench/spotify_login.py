"""One-time Spotify login: saves SPOTIFY_REFRESH_TOKEN to .env.

Needs SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in .env, and the redirect URI below
registered on the app. You log in yourself in the browser. No token is ever printed.

    uv run python -m bench.spotify_login
"""

from __future__ import annotations

import os
import secrets
import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from dotenv import load_dotenv, set_key

REPO_ROOT = Path(__file__).resolve().parent.parent
REDIRECT_HOST = "127.0.0.1"  # Spotify rejects "localhost"
REDIRECT_PORT = 8888
REDIRECT_URI = f"http://{REDIRECT_HOST}:{REDIRECT_PORT}/callback"
SCOPES = "user-read-playback-state user-modify-playback-state"
AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
LOGIN_TIMEOUT_S = 180


class LoginFailed(RuntimeError):
    """The browser login or the token exchange did not succeed."""


def authorize_url(client_id: str, state: str) -> str:
    query = urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPES,
            "state": state,
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def code_from_callback(target: str, state: str) -> str:
    """Pull the code out of the redirect target, refusing a wrong state or a denied login."""
    query = parse_qs(urlsplit(target).query)
    if query.get("state", [""])[0] != state:
        raise LoginFailed("state did not match, so this redirect was not ours")
    if "error" in query:
        raise LoginFailed(f"Spotify refused the login: {query['error'][0]}")
    code = query.get("code", [""])[0]
    if not code:
        raise LoginFailed("the redirect carried no code")
    return code


def exchange_code(client: httpx.Client, code: str, client_id: str, client_secret: str) -> str:
    """Trade the code for a refresh token. Only the status goes in errors, never the body."""
    r = client.post(
        TOKEN_URL,
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI},
        auth=(client_id, client_secret),
    )
    if r.status_code != 200:
        raise LoginFailed(f"token exchange returned HTTP {r.status_code}")
    token = r.json().get("refresh_token")
    if not token:
        raise LoginFailed("token exchange returned no refresh token")
    return token


def wait_for_callback(state: str, timeout_s: float = LOGIN_TIMEOUT_S) -> str:
    """Serve the redirect URI until the callback path arrives, and return its code."""
    result: dict[str, str | LoginFailed] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if urlsplit(self.path).path != "/callback":
                self.send_error(404)
                return
            try:
                result["code"] = code_from_callback(self.path, state)
                body = b"Spotify login finished. You can close this tab."
            except LoginFailed as e:
                result["error"] = e
                body = b"Spotify login failed. See the terminal."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass  # the request line holds the code

    deadline = time.monotonic() + timeout_s
    with HTTPServer((REDIRECT_HOST, REDIRECT_PORT), Handler) as server:
        server.timeout = 1.0
        while not result:
            if time.monotonic() > deadline:
                raise LoginFailed(f"no login within {timeout_s:.0f} s")
            server.handle_request()
    if "error" in result:
        raise result["error"]
    return result["code"]


def main() -> int:
    env_path = REPO_ROOT / ".env"
    load_dotenv(env_path)
    client_id = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        print("Add SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET to .env first.")
        return 1
    state = secrets.token_urlsafe(16)
    url = authorize_url(client_id, state)
    print(f"Opening the browser. If it does not open, visit:\n{url}")
    webbrowser.open(url)
    try:
        code = wait_for_callback(state)
        with httpx.Client(timeout=10) as client:
            token = exchange_code(client, code, client_id, client_secret)
    except LoginFailed as e:
        print(f"Login failed: {e}")
        return 1
    set_key(str(env_path), "SPOTIFY_REFRESH_TOKEN", token)
    print("Saved SPOTIFY_REFRESH_TOKEN to .env.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
