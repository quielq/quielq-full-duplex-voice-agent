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

That spike checked status codes only, so it did not show that audio came out.

## Playback finding

A live run on Nov 1, 2026 said "now playing" and nothing played. On the open Mac app, listed as the active device:

- Web API play with 10 URIs, then with one URI: 204, and the app stayed stopped with no track for the next 5 s. `GET /me/player` showed `is_playing` false.
- Web API play with the app paused on a loaded track: 204, and the app dropped that track and went to stopped.
- AppleScript `play track` in the same app: audio started at once, and `GET /me/player` showed the track playing.

The cause on Spotify's side is unknown. Pause through the Web API did work on the playing app.

A second test the same day, with a track loaded in the Mac app: Web API pause and resume kept the position, Web API next and previous returned 200, though the track names did not show a change because both were called "Dive". AppleScript next and previous changed the track. Queueing search results returned 200, but next after "Dive" gave "Dive - Acoustic". Playing a track in its album context made next go to the album's next song. One AppleScript play passed the 2.5 s limit while the app did start the song, so a timeout now leaves the decision to the start check.

## Decision

- `servers/tasks/music/backends.py` holds `MockBackend` (the default) and `SpotifyBackend`. The tool names stay `play_music`, `stop_music`, `set_volume`, and `get_now_playing`, so the task suite stays on the mock.
- `--stages music=spotify` selects Spotify. The bot passes it to the server as `ME3_MUSIC_BACKEND`.
- `play_music` queues the search results over the Web API, so a mood keeps playing after one track. The result carries `now_playing`, the track Spotify started, and the reply template uses it.
- On macOS, when the chosen device is a computer, `play_music` starts the first result inside its album in the local app through AppleScript instead. The album supplies the next songs, so a mood stays in one album. This assumes the listed computer is this Mac.
- `resume_music`, `next_track`, and `previous_track` use the Web API on every device, and `stop_music` is a pause. Each reports the track Spotify shows afterwards, and fails if the player did not move. `previous_track` may restart the current song, as Spotify decides.
- `play_music` succeeds only once `GET /me/player` shows an asked-for track playing, checked for about 3 s. A 204 alone is not success, and a track that was already playing does not count.
- Device order is the active device, then a computer, then anything else. A smart speaker never starts unless it is already active or the only device.
- A failure returns `{"error": ...}`, which the harness treats as failed, so the success template cannot claim that music started. The text never holds a response body.
- Login is the authorization code flow, once, through `bench/spotify_login.py`. The refresh token lives in `.env`, and the access token stays in memory.
- Only the music server receives `SPOTIFY_*` variables. Every other server still gets no credentials.
- Every request goes through the ledger at $0 under a $0.01 cap, because a zero cap refuses and the API is free.

## Alternatives

- `search_and_play`, `pause`, `next`, and `previous` from the review: `next` and `previous` were added once Quiel asked for them, as `next_track` and `previous_track`, because tool names must be unique across servers. The other two already exist under the names above.
- Queueing search results after a local play: it needs up to nine more requests per play, and a song search mostly returns other versions of that song.
- Authorization code with PKCE needs no client secret, but its refresh tokens rotate on each use. The chosen flow did not rotate in the spike, and a rotated token is still saved if one arrives.
- Playing Spotify audio through the harness: Spotify's terms and the Web API do not provide it, and it would need a device library such as librespot. Audio stays in the Spotify app.

## Consequences

- A Spotify app must be open. If none is, ME3 says so.
- ME3 cannot duck Spotify. Lowering it would take a Web API volume call, slower than the 100 ms duck rule, and it is not built.
- Music on speakers can reach the mic. Use headphones.
- The first AppleScript call may make macOS ask to let the terminal control Spotify. Until that is allowed, ME3 says it could not start the app.
- `play_music` takes up to about 3 s longer on a failure, and a successful play adds at least one request.
- The Pi has no Spotify app. Phase 2 plan (Quiel, Nov 1, 2026): speakers on the Pi, and a web app on the Pi that plays the music. It is not built, and it needs its own ADR. It would use the Web API path, so the start check covers it, and it must first show that the Web API can start that player. The likely route, Spotify's Web Playback SDK, still has to be checked against current docs, including whether it runs in a Pi browser.
- Spotify remains demo only, as the review says.

## Sources

- https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide
- https://developer.spotify.com/documentation/web-api/references/changes/february-2026
- https://developer.spotify.com/blog/2026-07-23-web-api-quota-updates
- https://developer.spotify.com/documentation/web-api/concepts/quota-modes
- https://developer.spotify.com/documentation/web-api/concepts/redirect_uri
- https://developer.spotify.com/documentation/web-api/reference/start-a-users-playback
