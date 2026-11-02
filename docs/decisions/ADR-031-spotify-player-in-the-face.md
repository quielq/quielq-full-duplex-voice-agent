# ADR-031: Spotify plays inside the robot face

Date: Nov 2, 2026. Status: requested by Quiel on Nov 2, 2026. Built and off by default. Played on the Pi on Nov 2, 2026.

## Context

ADR-022 controls a Spotify app that runs somewhere else. The Pi has raspotify from the VCM project, and a spike on Nov 2 played a track on it through the Web API. Quiel wants the agent to play music itself, with no second app to look after. raspotify also has no saved login, so it needs picking in a Spotify app after each reboot.

## Spike

Run on the Pi on Nov 2, 2026, `20261102-113457-c40ee0`, using raspotify, music at 50%, nobody talking: about 2 minutes of jazz piano and Christmas songs gave no Silero onsets, no transcripts, and no replies, $0.014. The mic was at -3 to -9 dB, close to clipping. Nobody spoke, so hearing a person over music is untested.

Player run on the Pi on Nov 2, 2026, `20261102-120127-3f5a77`, fake voice stages: the page reported `ready`, Spotify listed `ME3`, the real `play` path started a track, and about 83 s played through Chromium into the SoundBar at volume 50 with no player errors. Temperature stayed at 52 to 58 C and peak load was 2.39, with the face open. The volume reading lagged the change by some seconds.

## Decision

- The face page loads Spotify's Web Playback SDK and registers as a Spotify device named `ME3`.
- `--spotify-player` or `ui.spotify_player` turns it on, with the face and `music=spotify`. It is off by default.
- The page only plays. Choosing, pausing, and skipping stay on the `music` tools, so the screen still cannot bypass the tool policy (ADR-027).
- With the player on, `play` uses `ME3` by name and refuses if it is missing. It never falls back to another device, because that started a Xiaomi speaker in the ADR-022 spike.
- The page gets a short-lived access token from `/api/spotify-token`. The refresh token and client secret stay in the harness. The route checks Host and Origin, is never logged, and is off unless the player is on.
- The SDK script is a separate file, served only when the player is on, so the face still makes no outside requests by default.
- Login needs the added `streaming`, `user-read-email`, and `user-read-private` scopes, so `bench.spotify_login` must run once more.

## Alternatives

- **raspotify as a Spotify Connect device:** proven, and lighter on the Pi, but a second service with its own login, and it depends on what VCM installed.
- **Own client on the Spotify protocol:** librespot is the way to do this in Python or Rust, and it is far larger than a page module. It is what raspotify already is.

## Consequences

- Music needs the face open in Chromium, with Widevine and autoplay allowed. The kiosk launcher sets the autoplay flag. Another browser window needs a tap on the page first.
- Heat with live voice and music together is unmeasured. The Pi reached 78 C in the first live run, and the player run stayed under 58 C on fakes.
- Full Premium is required. Spotify lists mobile-only plans as unsupported.
- The echo canceller still does not hear the music, because it is not in the reference. Hearing a person over music is the open risk, wherever the music plays.

## Sources

- https://developer.spotify.com/documentation/web-playback-sdk
- docs/decisions/ADR-022-spotify-music-backend.md
- docs/decisions/ADR-027-browser-robot-face.md
