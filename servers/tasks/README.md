# Task servers

`clock`, `notes` (SQLite), `thermostat` (mock), `comms` (mock, nothing is really sent), `lights` (mock, no hardware), and `music` (mock by default, Spotify with `--stages music=spotify`, ADR-022) are built. The bot starts all six; `--servers` narrows that. `knowledge` and `delegate` wait for Thursday's command list, and the real Xiaomi backend waits on the checks in `TODO.md`. Tool names must be unique across servers, and every tool carries all four MCP annotation hints.
