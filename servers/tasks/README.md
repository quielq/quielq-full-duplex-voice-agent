# Task servers

Ten FastMCP servers, one process each: `clock`, `notes`, `docs`, `workspace`, `knowledge`, `music`, `comms`, `lights`, `thermostat`, and `delegate`. The bot starts all ten, and `--servers clock,notes` narrows that. A document agent starts only `docs`, scoped by `ME3_DOC_SCOPE` (ADR-039).

`docs/tools.md` lists every tool, what it does by default, how to make it real, and how to add a new one. `docs/codebase.md` shows how a server is wired into the harness. Tool names must be unique across servers, and every tool carries all four MCP annotation hints.
