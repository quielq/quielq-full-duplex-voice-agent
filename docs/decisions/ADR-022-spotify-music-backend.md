# ADR-022: Spotify music backend

Date: Nov 1, 2026. Status: approved by Quiel, Nov 1, 2026. Departs from the review.

## Context

The `music` server was a mock, so "play some jazz" logged success and played nothing. The review calls for the Spotify Web API, for demos only, with the tools `play`, `pause`, `next`, `previous`, `set_volume`, and `search_and_play`.

Spotify tightened Development Mode in 2026, after the review was written:

- The app owner needs Premium, and the app stops working if it lapses.
- `GET /search` has a `limit` maximum of 10 and a default of 5. In the Nov 1 spike, `limit=10` for "jazz" returned 5 tracks.
- Library, browse, and batch endpoints were removed. Player endpoints are listed as still available.
- A July 2026 post raised the limit to 25 Client IDs per developer, and counts quota per developer account.

## Spike

Run Nov 1, 2026 against Quiel's Development Mode app: token refresh 200, devices 200, search 200, play 204, player state 200, volume 204, pause 200. A second pause also returned 200.

With no active device, taking the first device started a Xiaomi speaker rather than the Mac.

## Decision

- `servers/tasks/music/backends.py` holds `MockBackend` (the default) and `SpotifyBackend`. The tool names stay `play_music`, `stop_music`, `set_volume`, and `get_now_playing`, so the task suite stays on the mock.
- `--stages music=spotify` selects Spotify. The bot passes it to the server as `ME3_MUSIC_BACKEND`.
- `play_music` queues the search results, so a mood keeps playing after one track. The result carries `now_playing`, the track Spotify started, and the reply template uses it.
- Device order is the active device, then a computer, then anything else. A smart speaker never starts unless it is already active or the only device.
- A failure returns `{"error": ...}`, which the harness treats as failed, so the success template cannot claim that music started. The text never holds a response body.
- Login is the authorization code flow, once, through `bench/spotify_login.py`. The refresh token lives in `.env`, and the access token stays in memory.
- Only the music server receives `SPOTIFY_*` variables. Every other server still gets no credentials.
- Every request goes through the ledger at $0 under a $0.01 cap, because a zero cap refuses and the API is free.

## Alternatives

- `search_and_play`, `pause`, `next`, and `previous` from the review: `next` and `previous` wait until they are asked for, and the other two already exist under the names above.
- Authorization code with PKCE needs no client secret, but its refresh tokens rotate on each use. The chosen flow did not rotate in the spike, and a rotated token is still saved if one arrives.
- Playing Spotify audio through the harness: Spotify's terms and the Web API do not provide it, and it would need a device library such as librespot. Audio stays in the Spotify app.

## Consequences

- A Spotify app must be open. If none is, ME3 says so.
- ME3 cannot duck Spotify. Lowering it would take a Web API volume call, slower than the 100 ms duck rule, and it is not built.
- Music on speakers can reach the mic. Use headphones.
- The Pi has no Spotify app, so Phase 2 needs a separate device.
- Spotify remains demo only, as the review says.

## Sources

- https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide
- https://developer.spotify.com/documentation/web-api/references/changes/february-2026
- https://developer.spotify.com/blog/2026-07-23-web-api-quota-updates
- https://developer.spotify.com/documentation/web-api/concepts/quota-modes
- https://developer.spotify.com/documentation/web-api/concepts/redirect_uri
- https://developer.spotify.com/documentation/web-api/reference/start-a-users-playback
