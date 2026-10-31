# Task servers

`clock`, `notes` (SQLite), `thermostat` (mock), and `comms` (mock, nothing is really sent) are built. The bot starts all four; `--servers` narrows that. The others (music, lights, knowledge, delegate) wait for Thursday's command list. Tool names must be unique across servers, and every tool carries all four MCP annotation hints.
