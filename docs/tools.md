# ME3 tools: how they work, how to add one, and what keeps them safe

ME3's tools are MCP servers that the voice LLM can call. A spoken request can read a file, search the web, send a message, or run a shell command, so every tool sits behind limits that hold even if the model is wrong or has been fooled. This page explains how a call flows, how to add a tool, and why each guardrail exists.

Decisions and evidence are in ADR-009, ADR-011, ADR-021, ADR-023, ADR-024, ADR-026, ADR-028, and ADR-035.

## How a tool call flows

```
"Text Ana that I'm late"
  │  speech to text
  ▼
LLM picks a tool: send_message(to="Ana", body="I'm late")
  │
  ▼
harness/tool_policy.py        hints and configs/tool_policy.yaml decide what happens
  │  guard       refuse a bad shell command before anyone is asked
  │  read-back   "Send Ana this message: I'm late. Should I send it?"
  │  wait        only a transcript can answer, and only after the question has played
  │  undo        "Say cancel in the next 5 seconds to stop it"
  ▼
MCP call, stdio or streamable HTTP, to one server process
  │
  ▼
servers/tasks/comms/server.py  limits that hold whatever the harness did
  │  saved contacts only, daily cap through bench/cost_ledger.py
  ▼
the real backend, then the result goes back to the LLM to be spoken
```

The pieces:

| Piece | Job |
| --- | --- |
| `servers/tasks/<name>/server.py` | One FastMCP server. Its tools, hints, and limits |
| `servers/common.py` | Logging, data paths, `tool_stage`, the ledger, path checks |
| `harness/mcp_tools.py` | Starts servers, and decides which secrets each one receives |
| `harness/tool_policy.py` | Runs at once, holds, asks, or refuses each call |
| `configs/tool_policy.yaml` | Per tool overrides, read-backs, phrases, containment |
| `configs/providers.yaml` | Which backend each tool uses, caps, prices |
| `servers/command_policy.py`, `servers/sandbox.py` | Shell command policy (ADR-034) and the OS sandbox |

Each server is its own process, so a server gets only its own secrets, and a crash stays contained. The LLM sees one flat list of tools, which is why names must be unique across servers.

## Current tools

"Default" is a plain `harness.bot` run.

| Server | Tools | Default | To make it real |
| --- | --- | --- | --- |
| `clock` | `get_time`, `set_timer`, `set_alarm`, `list_timers`, `cancel_timer` | Real. The bot says when one is due (`harness/timer_watch.py`) | |
| `notes` | `add_note`, `list_notes`, `delete_note` | Real, SQLite | |
| `docs` | `index_documents`, `local_search`, `search_document`, `list_documents`, `read_document` | Real, SQLite FTS5 | Put files in `data/documents`, which the agent can read but never write. A document agent sees only its folders (ADR-039) |
| `workspace` | `read_file`, `list_files`, `write_file`, `edit_file`, `grep`, `serve_status` | Real, confined to `data/workspace` | |
| `workspace` | `bash`, `serve` | Mock | `ME3_WORKSPACE_LIVE=1` and a working sandbox |
| `workspace` | `open_in_browser` | Mock | `ME3_WORKSPACE_LIVE=1`. Runs outside the sandbox, HTML in the workspace only (ADR-026) |
| `workspace` | `send_file` | Always a mock | Copies to `data/send_outbox` |
| `knowledge` | `get_weather` | Real, Open-Meteo, no key | |
| `knowledge` | `fetch_content` | Real, plain HTTP | |
| `knowledge` | `search` | Fake | `ME3_TOOL_STAGES=search=tavily` |
| `music` | `play_music`, `stop_music`, `resume_music`, `next_track`, `previous_track`, `restart_track`, `set_volume`, `get_now_playing` | Fake | `--stages music=spotify` |
| `comms` | `send_message`, `call_contact`, `list_contacts`, `list_sent_messages` | Mock | `ME3_TOOL_STAGES=comms=bridge`, see below |
| `lights` | `set_light`, `list_lights` | Mock | No hardware yet |
| `thermostat` | `set_thermostat`, `get_thermostat` | Mock | No hardware yet |
| `delegate` | `start_task`, `task_status`, `task_result`, `cancel_task` | Fake worker model. Its file tools are real, in `data/workspace` | `ME3_TOOL_STAGES=delegate=anthropic` (Haiku 5.5, ADR-035) |

`ME3_TOOL_STAGES` takes several at once: `search=tavily,comms=bridge`.

## Voice tools

The voice servers are MCP servers too, but the harness calls them, not the LLM, so they are not in the tool list the model sees and `tool_policy.py` does not hold them. They carry control only. Audio rides a Unix socket per stream, and events are logging notifications on the `voice` logger while `listen` or `speak` is open (ADR-008, ADR-032).

| Server | Tools | Purpose | Status |
| --- | --- | --- | --- |
| `voice-listen` | `listen`, `stop_listening`, `status` | Holds one Deepgram Flux stream per `listen` call and sends transcript and turn events | Live on the Mac, Nov 4 (`stt=voice_listen`) |
| `voice-speak` | `speak`, `stop`, `status` | Holds one Cartesia stream per `speak` call, with word timestamps for `played_up_to` | Live on the Mac, Nov 4 (`tts=voice_speak`) |
| stubs | the above, plus `duck` and `unduck` in `voice-speak` | The bridge probe (ADR-008). Ducking and playback stay in the harness, so `duck` and `unduck` exist only here | Fakes only |

Their limits:

- **Key stays in the server.** `SERVER_KEYS` in `harness/mcp_tools.py` gives `voice-listen` only the Deepgram key and `voice-speak` only the Cartesia key. The harness never sees them.
- **Fixed upstream.** The provider address is fixed per server, and the harness supplies only a `key=value` query string, so a caller cannot point the key anywhere else. Tests may name a loopback upstream through an environment variable, and nothing else.
- **Private socket.** Each stream gets a socket in a 0700 folder, set to 0600, and the first frame must be a one-time token compared in constant time.
- **Cost ledger.** Each `listen` or `speak` call checks the provider's daily cap before it opens a stream.
- **Recovery.** A dropped stream is reopened by Pipecat's reconnect logic through a new call, and a dead server is restarted. Tested on fakes, stdio and HTTP.

Proven live on Nov 4: an 18 turn mic run through both servers (`20261104-202417-64248a`, $0.21), where both streams stayed up for the whole 5 minute run and voice interruption worked, and the interruption bench through `voice-speak` (`20261104-202147-ab3d44`, $0.0246, 9 words heard, 10 committed). The stored waiting, network-loss, and cap clips were also made through `voice-speak`. Not yet run on the Pi.

## Add a tool

Adding one to an existing server takes steps 2 to 6. A new server needs all of them. `servers/tasks/notes/server.py` is the shortest example.

**1. Create the server** in `servers/tasks/<name>/`, with an empty `__init__.py`, and add it to `TASK_SERVERS` in `harness/mcp_tools.py`.

**2. Write the tool.**

```python
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from servers.common import ToolCallLog, serve

mcp = FastMCP("tides", instructions="Tide times.")
mcp.add_middleware(ToolCallLog("tides"))


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}
)
def get_tides(place: Annotated[str, Field(min_length=1, description="Coastal town")]) -> dict:
    """Get today's high and low tides."""
    ...


if __name__ == "__main__":
    serve(mcp)
```

- Set all four hints honestly. Harness policy follows from them (next section).
- The docstring is what the model reads to choose a tool. Keep it to one or two short sentences.
- Pick a specific name that no other server uses, such as `list_timers`, not `list`.
- Bound every argument with `Field`, so a bad call fails before your code runs.
- Return an `{"error": "..."}` dict for anything the user should hear. An error in plain words is something the model can say.
- Keep results short. Everything returned goes into the prompt. Limit text to about 4,000 characters.
- Keep state under `data_dir()`, so tests never touch real data.

**3. Choose a backend.** If the tool calls an outside service, add a stage to `tool_stages` in `configs/providers.yaml`, and read it with `tool_stage("tides")`. Keep a fake that needs no network.

**4. Go through the ledger.** A provider call needs a provider entry with a cap and a price. Rule 7 applies even to free APIs: a cap of zero refuses the call, so it is also the off switch.

```python
books = ledger()
try:
    books.authorize("tides_api")
except SpendRefused as e:
    return {"error": str(e)}
result = _fetch(place)                                  # no retry on anything with a side effect
books.record("tides_api", "lookup", 1, price("tides_per_call"))
```

**5. Give it only its own secret.** Keys come from `.env`, and `_server_env` in `harness/mcp_tools.py` strips every key by default. Add yours to `SERVER_KEYS` for that one module. Never put a key in a tool result or a log line.

**6. Set the policy and test it.**

- Add a rule in `configs/tool_policy.yaml` if the defaults do not fit. A slow API needs a `timeout_s`, and anything that asks for a yes needs a `readback`.
- Add a line to the table above. `tests/test_tool_docs.py` fails until you do.
- Add a server test with no network, as in `tests/test_tool_servers.py`.
- Add a task case in `bench/task_suite/cases/`, and a rule in `harness/fakes.py` so the fake LLM calls it.
- Run `uv run pytest -q`, `uv run python -m bench.task_runner`, and `uv run python -m bench.doctor --offline`.
- Check both MCP transports: stdio and streamable HTTP.

A new real backend also needs a recorded approval, as in `configs/providers.yaml`. Then run it once live: fakes did not catch the PDF crash found on the first live `fetch_content`.

## Which guardrails a tool needs

Start from the worst thing a wrong call could do. The model can be mistaken, can mishear, or can be steered by text in a page it read.

| If the tool | Hints | It gets |
| --- | --- | --- |
| Only reads | `readOnly` true | Runs at once |
| Writes, safe to repeat or easy to fix | `readOnly` false, `destructive` false | Runs at once |
| Destroys something local | `destructive` true, `openWorld` false | 5 s undo window. `write_file` and `edit_file` skip it and keep the old version instead (ADR-028) |
| Reaches people or the outside | `destructive` true, `openWorld` true | Read-back, yes, then undo window |
| Has missing hints | | Spoken confirmation |
| Runs code | Same as the row above | Guard, sandbox, and off behind a flag |

`permission: disabled` in `configs/tool_policy.yaml` removes a tool entirely, including from the schema.

Hints are only hints. A server can lie about them, so the harness and the server each enforce their own limits. Add a limit to the server whenever the rule must hold even if the harness has a bug.

## The guardrails, and why each exists

| Guardrail | Where | The failure it stops | What it does not stop |
| --- | --- | --- | --- |
| Policy from hints | `tool_policy.py` | A destructive call running straight from a misheard sentence | A tool with false hints |
| Spoken read-back | `tool_policy.py` | A model describing an action more kindly than it is, whether by mistake or from injected text | A user saying yes without listening |
| Yes after the question | `tool_policy.py` | The bot's own voice, echoed back, approving the action | Another person in the room saying yes |
| Undo window | `tool_policy.py` | A confirmed mistake noticed a moment later | Anything after the window |
| Refuse what is too long to read | `tool_policy.py` | Hiding a payload past what a person can hear | |
| Timeouts | `tool_policy.py` | A hung tool holding up the voice loop | A tool that finished after its timeout |
| Cost ledger | `bench/cost_ledger.py` | A loop draining money or sending without limit | Carrier SMS charges, which it cannot see |
| Secrets per server | `harness/mcp_tools.py` | One server leaking another's key | A server leaking its own |
| Folder roots | `servers/common.py` | `../../.env` and symlinks out of the workspace | |
| Saved contacts only | `comms/server.py` | Injected text naming a new recipient | A wrong message to a real contact, which is what the read-back is for |
| Private addresses refused | `knowledge/server.py` | A page steering `fetch_content` at your router or local services | |
| Untrusted note on results | `knowledge/server.py` | Some prompt injection. This is a soft limit only | A determined injected instruction |
| Command policy | `servers/command_policy.py` | Obvious danger, installs, network tools, and paths outside the workspace, refused with a reason before anyone is asked | An interpreter after a yes, such as `python3` |
| OS sandbox | `servers/sandbox.py` | Everything a command could do outside the workspace | Damage inside the workspace |
| Containment | `tool_policy.py` | A steered model that keeps trying | Fewer than three critical attempts |
| Audit log | Event log, `data/tool_calls.jsonl` | Not knowing what happened | |

Two rules sit behind this table:

- **Tool output is data.** Text from a page or file can reach the model, so a limit that depends on the model behaving is not a limit. `RESULT_NOTE` in ADR-023 is fixed text for the same reason.
- **Fail closed.** No sandbox means no command. An unreadable command is refused. An unanswered question means no. A dropped undo announcement means the action does not run.

## Multi-stage jobs

A job like "plan and build a website" is one spoken request followed by many tool calls. The system prompt tells the model to say one sentence, write `plan.md` in a folder for the job, work through the steps without asking to continue, check the result, and offer to open it.

Three things make that work (ADR-028):

- `write_file` and `edit_file` run at once, so a file exists for the next step. The server copies anything it replaces to `data/workspace/.versions`, and the result names the copy.
- The LLM can write 8,192 tokens in one reply, and `write_file` can append for a longer file.
- For a real job, turn on the tools that are mocks or fakes by default:

```bash
ME3_WORKSPACE_LIVE=1 ME3_TOOL_STAGES=search=tavily uv run python -m harness.bot --stages stt=deepgram_flux,llm=anthropic,tts=cartesia --echo-cancel --ui
```

A `bash` read such as `ls` or `grep` runs at once (ADR-034). A `bash` change or run, and every `serve` and `open_in_browser` call, asks for a spoken yes. That is deliberate: those are the tools that can do real harm.

## Background tasks

`start_task` hands a job to a worker and returns an id at once, so the voice loop keeps listening (ADR-035). The worker is a short tool loop on Haiku 5.5, or a scripted fake by default.

- **It can do only what runs at once in the foreground:** `read_file`, `list_files`, `write_file`, `edit_file`, `grep`, and `bash` commands that `servers/command_policy.py` returns as run. It calls the workspace server's own functions, so the same folder roots and `.versions` copies apply.
- **Anything that needs a yes ends the task, unrun:** a `bash` change or run, `serve`, `open_in_browser`, messages, and calls. The result says "It needs your yes for: ..." in the policy's words, and the user asks for that step in the foreground. A critical refusal ends the task too.
- **Limits:** 12 model calls and $0.05 per task, two tasks at once, and the `anthropic` daily cap in the ledger. A zero cap refuses before `start_task` returns.
- **The announcement:** `harness/delegate_watch.py` says "Your background task is done" when nobody is talking, no reply is streaming, and no question is pending. A cancelled task stays quiet.
- `cancel_task` has the usual 5 s undo window. It stops between steps, and files already written stay.

## Bash and serve

`bash` and `serve` are the only tools that can do anything, so they get four layers. Each covers a gap in the one before it.

1. **Off by default.** Both are mocks unless `ME3_WORKSPACE_LIVE=1`.
2. **Command policy.** `servers/command_policy.py` (ADR-034, written for this project) reads only simple commands: words, quotes, pipes, `&&`, `||`, `;`, and plain redirects. Anything else is refused rather than guessed at. The result is run, ask, or refuse.
   - **Run at once:** reads, such as `ls`, `cat`, `grep`, `find`, `wc`, and `git status`.
   - **Ask:** anything that writes, deletes, or runs code or a build, and any program on no list, which the question names.
   - **Refuse:** installs ("installing packages is off: the sandbox has no network"), network tools, the desktop, paths outside the workspace, and `$(...)`, backticks, `<(...)`, here-documents, loops, functions, background `&`, and a variable as the program.
   - **Refuse, critical:** `sudo`, `docker`, `crontab`, disk, kernel, and system commands, deleting from the root or home, and anything touching `.env` or `.ssh`. Saying yes cannot lift these.
   - Before `rm` or `rmdir` runs, the server copies what it deletes to `data/workspace/.versions`, and the result names the copies.
   - The harness checks before asking, and the server checks again before running.
3. **Spoken read-back.** The harness says what the command does in plain words, built from the command itself: "This will delete build and everything inside, then run make_site.py with Python." The face shows the exact command text while it waits. The model does not write this question.
4. **OS sandbox.** A command can write only in the workspace, read only that, the documents folder, and system files, and has no network. `python3` can reach anything the sandbox allows, and nothing more. That is why the sandbox is the real boundary, and the policy is the polite one.

`serve` may also listen on this machine. Three critical refusals in one session lock `bash`, `serve`, `open_in_browser`, file writes, and messaging until the session ends.

| | Mac | Pi |
| --- | --- | --- |
| Sandbox | `sandbox-exec` (deprecated by Apple, still ships) | `bwrap` (`apt install bubblewrap`) |
| Tested | Real escape attempts in `tests/test_shell_guardrails.py` | Arguments only, because `bwrap` does not run on a Mac |
| `serve` network | This machine only | Host network, so a page can be opened from the LAN. A service can reach out too |

`uv run python -m bench.doctor` runs a real read-outside test on either machine, and warns if it fails.

To turn them on: `ME3_WORKSPACE_LIVE=1 uv run python -m harness.bot ...`.

### What this does not cover

- Damage inside `data/workspace`. Keep nothing there you cannot lose.
- On the Pi, `serve` has outbound network. Treat a started service as trusted.
- A page shown by `open_in_browser` runs in your real browser, with network, outside the sandbox. The read-back names the file, not what it contains (ADR-026).

## Messages and calls through the Mac bridge

`scripts/mac_phone_bridge.py` runs on the Mac and drives Messages and Continuity calling. ME3 reaches it over HTTP, from the same Mac or from the Pi.

```
Mac or Pi                                     Mac
comms server  ── HTTP + token ──▶  mac_phone_bridge.py  ── Messages app ──▶ iPhone
```

**Mac setup, once**

1. Follow the Mac setup in the docstring of `scripts/mac_phone_bridge.py`.
2. Put a token in `.env`, from `openssl rand -hex 16`, as `ME3_PHONE_BRIDGE_TOKEN`.
3. Set `ME3_PHONE_BRIDGE_URL=http://127.0.0.1:8765` in `.env`.
4. Copy `configs/contacts.example.yaml` to `data/contacts.yaml` and add real numbers.

```bash
uv run --env-file .env python scripts/mac_phone_bridge.py --dry-run
```

Then run the bot with `ME3_TOOL_STAGES=comms=bridge`. Remove `--dry-run` when you want real sends.

**On the Pi**

1. Use the same token, and `ME3_PHONE_BRIDGE_URL=http://<mac-name>.local:8765`. `scutil --get LocalHostName` gives the name.
2. Copy `data/contacts.yaml` to the Pi.
3. Start the bridge on the Mac with the Pi named:

```bash
uv run --env-file .env python scripts/mac_phone_bridge.py --host 0.0.0.0 --allow-from <pi ip>
```

**Limits**

- Calls are not hands-free. macOS shows a Call button, and someone must click it. On the Pi, that means someone at the Mac.
- The Mac must be awake and signed in, with the same Apple ID as the iPhone.
- The link is plain HTTP, so the token and messages can be read on that network. Use a network you trust.
- 20 sends a day, set by `phone_bridge` in `configs/providers.yaml`. A cap of zero switches it off.
- A failed send is never retried, because a timeout may still have sent.

## Moving to the Pi

Most of this needs no change, because tools are HTTP clients and Python.

| Works unchanged | Needs a step on the Pi |
| --- | --- |
| Weather, fetch, search | `apt install bubblewrap`, then `bench.doctor` must show the sandbox as ok |
| Files, notes, documents, clock | `.env` and `data/contacts.yaml` copied by hand. Neither is in git |
| Tool policy, read-back, containment | Bridge URL and `--allow-from`, as above |
| | Spotify runs on the Mac app only for now (ADR-022) |
| | `open_in_browser` covers the robot face in kiosk mode, and there is no way back by touch yet (ADR-027) |

The sandbox and the bridge are chosen at run time, so one branch serves both machines. Nothing has run on a Pi yet.
