"""The Spotify login helpers, on a mock transport. No network and no browser."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from bench.spotify_login import (
    REDIRECT_URI,
    LoginFailed,
    authorize_url,
    code_from_callback,
    exchange_code,
)


def _client(status: int, body: dict) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(status, json=body)))


def test_authorize_url_uses_loopback_and_only_the_player_scopes():
    query = parse_qs(urlsplit(authorize_url("cid", "st")).query)
    assert query["redirect_uri"] == [REDIRECT_URI]
    assert "localhost" not in REDIRECT_URI
    assert query["scope"] == ["user-read-playback-state user-modify-playback-state"]
    assert query["state"] == ["st"]


def test_callback_returns_the_code():
    assert code_from_callback("/callback?code=abc&state=st", "st") == "abc"


@pytest.mark.parametrize(
    "target",
    ["/callback?code=abc&state=other", "/callback?code=abc", "/callback?error=access_denied&state=st", "/callback?state=st"],
)
def test_callback_refuses_bad_redirects(target):
    with pytest.raises(LoginFailed):
        code_from_callback(target, "st")


def test_exchange_returns_the_refresh_token():
    assert exchange_code(_client(200, {"refresh_token": "rt"}), "c", "id", "sec") == "rt"


def test_exchange_error_does_not_echo_the_response_body():
    with pytest.raises(LoginFailed) as e:
        exchange_code(_client(400, {"error": "secret-looking-body"}), "c", "id", "sec")
    assert "secret-looking-body" not in str(e.value)
