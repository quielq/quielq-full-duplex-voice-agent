# ADR-027: A robot face and device panel in a browser

Date: Nov 2, 2026. Status: built, off by default. Requested by Quiel on Nov 2, 2026, before the Pi port. Runs on the Mac on fakes only. Not yet seen on the Pi or the 7-inch display.

## Context

The review has no screen. Quiel wants the Pi to show a robot face on a 7-inch touch display, as the main thing on screen, with the simulated lights and thermostat visible beside it. The face follows Quiel's four-expression robot artwork: angry, happy, neutral, and sad.

## Decision

`--ui` or `ui.enabled` starts a small aiohttp server inside the bot process. Chromium on the same Pi opens it in kiosk mode.

- `harness/ui.py` listens to `EventLog`, so the page sees what the logs record and the pipeline gains no new path. The only addition is a callback for reply text as it streams.
- The page is one file, `harness/ui_page.html`, with no outside requests, so the face still works when the network does not. The robot is redrawn as SVG, so each expression can blink and talk. The artwork file is not embedded.
- The four expressions come from outcomes: happy on a tool success, sad on a failure or an apology, angry when the shell guard or containment refuses something. Listening, thinking, working, asking, and muted were added from the same drawing.
- The mouth moves in a made-up rhythm, because the page receives no audio.
- Lights and thermostat are read from the JSON files the mock servers write. The panel only displays them, so nothing on screen can get around the tool policy. Mute is the one control.
- The server binds to 127.0.0.1, and the mute endpoint refuses a request from another origin.
- Server-sent events carry state one way. They reconnect by themselves, and the page shows a sad face while the agent is down.

## Alternatives

- **A native or Qt app:** harder to style, and the browser was asked for.
- **WebSocket:** two-way traffic is not needed for one button.
- **Touch control of lights and thermostat:** would need its own path around the policy. Add it with an ADR if wanted.
- **LLM-chosen expressions:** a tool call or extra tokens on every turn, in a loop that is already above its latency target.

## Consequences

- The UI cost on the voice path is one short function per event. CPU use of the page on the Pi is unmeasured, and SVG glow filters were left out for that reason.
- `open_in_browser` opens a page over the kiosk face on the Pi, and touch has no way back yet.
- The screen shows transcripts to anyone in the room (`docs/privacy.md`).
- `aiohttp` becomes a direct dependency. It was already installed through Pipecat.

## Sources

- https://docs.aiohttp.org/en/stable/web_quickstart.html
- https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events
