# ADR-030: One user service on the Pi, and fakes until switched to live

Date: Nov 2, 2026. Status: approved with the Pi port plan by Quiel on Nov 2, 2026. Scripts built, not run on a Pi.

## Context

The review says to run each process as a systemd service with restart on failure, and to have a watchdog check that audio capture is alive. The bot starts its MCP servers itself as stdio children, and the default audio device on the Pi is PipeWire, which runs per user. The Pi also has to start by itself, which a system that spends money per turn must not do by accident.

## Decision

- One systemd user service, `me3-bot`, runs the bot. The MCP servers stay stdio children of the bot. This departs from the review's one service per process.
- It is a user service, so it can reach the user's PipeWire session. `loginctl enable-linger` starts it at boot.
- Restart on failure, at most 5 times in 2 minutes. A bot that stops at a daily cap exits cleanly and stays stopped.
- The service runs on fakes unless `~/.config/me3/bot.env` is edited to name live stages. Rule 2 of `CLAUDE.md` holds on boot too.
- Chromium starts from `~/.config/labwc/autostart`, the method in the Raspberry Pi kiosk tutorial, through `scripts/pi_kiosk.sh`, which waits for the face and restarts the browser if it crashes.
- The audio watchdog waits. It is the first thing cut if time runs short.

## Alternatives

- **One service per MCP server:** needs the HTTP transport on every server and more startup ordering, with no gain for a demo. If a child dies the bot's tools fail, and a restart of the whole bot covers it.
- **System service:** would not see the user's PipeWire session without extra setup.
- **Live stages in the unit file:** one reboot would start spending with nobody present.
- **Chromium as a systemd user unit:** a community pattern, but it depends on the desktop session target. The autostart file is the documented route.

## Consequences

- A crash in the bot takes the MCP servers with it and restarts them all, losing about the startup time.
- Going live on the Pi is a deliberate edit of one file.
- None of this has run on a Pi. Whether the image runs labwc, and how `pipewire` appears to PortAudio, are the first things to check (`docs/quickstart-pi.md`).

## Sources

- https://www.raspberrypi.com/tutorials/how-to-use-a-raspberry-pi-in-kiosk-mode/
- https://wiki.debian.org/PipeWire
- docs/architecture.md, failure table
