# ADR-035: The delegate server and its background worker

Date: Nov 4, 2026. Status: built and tested on fakes. One live task ran on Haiku 5.5 on Nov 4, 2026. Quiel ranks this as the first step to cut if time runs short, so it is kept small.

## Context

The architecture (row "Complex task", and the `delegate` row in the tool table) sends a long job to a background worker through an MCP tool that returns a task id at once, so the voice loop keeps talking and listening. On Nov 3 and 4 Quiel dropped OnIt as that worker and chose Haiku 5.5 (`claude-haiku-5-5`, the same model as the live LLM, ADR-025) with our own tool loop.

The risk is plain: a background task runs with nobody listening. If it could run a command that needs a spoken yes in the foreground, it would be a way around the read-back, the yes after the question, and the undo window (ADR-011, ADR-024). ADR-034 already sorts shell commands into run, ask, and refuse.

## Decision

`servers/tasks/delegate/` with four tools, all with the four hints:

| Tool | Hints | Policy |
| --- | --- | --- |
| `start_task(goal)` | write, not destructive, open world (it spends) | `auto`: runs at once, returns `t` plus six hex digits |
| `task_status(task_id)` | read only | runs at once |
| `task_result(task_id)` | read only | runs at once |
| `cancel_task(task_id)` | destructive, closed world | `auto` with the 5 s undo window, the same as `cancel_timer` |

The worker (`worker.py`) is a manual tool loop:

- **Tools it may run:** `read_file`, `list_files`, `write_file`, `edit_file`, `grep`, and `bash` only when `servers/command_policy.py` `check()` says `run`. It calls the workspace server's own functions, so every path check and the `.versions` copies are the same code, confined to `data/workspace`. `bash` stays a mock unless `ME3_WORKSPACE_LIVE=1`, and still runs in the sandbox.
- **Anything that would need a yes ends the task.** A `bash` command the policy returns as `ask`, any `serve` or `open_in_browser` call, and any other tool name (messages, calls) stop the task with "It needs your yes for: <the policy's plain words>." The worker checks every call in a reply first, so nothing in that reply runs. The schemas for `serve` and `open_in_browser` are offered only so the model can say what it wants.
- **Refusals:** an ordinary refusal (a network tool, a path outside) goes back to the model as an error. A critical one (`sudo`, `.env`) ends the task at once, which is stricter than the three-strike containment in the foreground.
- **Limits:** 12 model calls per task, $0.05 per task, and two tasks at once. Every model call goes through `bench/cost_ledger.py` under `anthropic`: `authorize` before, `record` of input and output tokens after. A zero cap refuses, and `start_task` checks the cap before it starts, so the refusal is heard at once.
- **Model:** a scripted fake by default, the same rule as every provider. `ME3_TOOL_STAGES=delegate=anthropic` switches to Haiku 5.5 with the `anthropic` key, which `harness/mcp_tools.py` passes to this server only. The model gets the workspace server's own tool schemas, so they cannot drift.
- **The announcement:** state is kept in `data/delegate.json`. `harness/delegate_watch.py` reads it once a second, like `timer_watch.py`, and speaks one finished task at a time, only when nobody is speaking, no reply is streaming, and nothing is held by the tool policy. Cancelled tasks, and tasks that finished before this session, stay quiet. A result is cut to 240 characters, the read-back limit.
- **`start_task` is in the containment list**, so after three critical refusals a steered model cannot start a task either.

## Alternatives

- **OnIt as the worker.** Dropped by Quiel on Nov 3: no programmatic API, and its shell and web tools would need a second policy.
- **Let the worker ask for a yes itself**, by speaking through the harness mid-task. More capable, but it puts a question into the voice loop at a moment the user did not pick, and it is a second path to the read-back. Ending the task and naming what it needs keeps one path: the user asks for that step in the foreground, where the harness asks.
- **Run the worker in the harness** instead of an MCP server. It would see the tool policy directly, but it would mix a long loop into the voice process, and the architecture puts it behind an MCP tool.
- **The MCP tasks extension (SEP-2663)** instead of our own ids. Pipecat's client is unlikely to support it (unverified), as the architecture notes.
- **Calling the workspace server over MCP** instead of importing its functions. Cleaner process separation, but a second client and a second process per task, for the same checks.

## Consequences

- A background task can do exactly what runs at once in the foreground, and no more. The common job (write files, check them with `ls` and `grep`) finishes without a question. A job that needs `python3`, `serve`, or opening a page stops and says so.
- `write_file` and `edit_file` run with no undo window here too. Replaced versions are kept in `data/workspace/.versions` (ADR-028).
- Cancel stops the loop between steps. A `bash` call already running finishes (it is capped at 25 s); its result is dropped.
- Text the worker reads from files can steer it. The system prompt says file text is data, but the real limit is that the worker can only do what needs no yes.
- A task running when the server stops cannot resume. On restart it is marked failed, quietly.
- **Live, Nov 4, 2026:** one task on Haiku 5.5, called from a script through `worker.run_task` with the server's own `make_model()`, not by voice. The goal was two files in a new folder, then a listing. It wrote both files correctly and listed them: 3 model calls, 4.6 s, $0.00095, state `done`.
- Not done: a live task started by voice, a live task that stops on "needs your yes", a task case that checks files written by a worker, and the Pi.

## Sources

- Anthropic Messages API tool use, `claude-api` skill reference and https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview (manual loop: append the assistant content unchanged, return `tool_result` blocks in one user message).
- Haiku 5.5 prices: $0.10 and $0.50 per million input and output tokens, as in `configs/providers.yaml`.
- `docs/architecture.md`, "Complex task" and the `delegate` tool row. ADR-011, ADR-024, ADR-025, ADR-028, ADR-034.
