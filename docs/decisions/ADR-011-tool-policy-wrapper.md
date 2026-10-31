# ADR-011: Tool policy wraps MCPClient handlers and owns the wait

Date: Oct 31, 2026. Status: built on `clock`. Implements section 3 of ADR-009.

## Context

ADR-009 needs a harness policy between `MCPClient` and the LLM, and Pipecat 1.13 documents no hook for it. A spike ran against the real clock server with a fake LLM, three arms, and an interruption sent from upstream while the action was held. An interruption sent from the LLM service itself never reaches its own `_handle_interruptions`, so the first run of the spike proved nothing and was redone.

What the installed package does (pipecat-ai 1.13.0):

* `FunctionSchema.handler` is read-only, but the constructor is public, so a wrapper means building a new schema. The handler is registered automatically from `LLMContext(tools=...)`.
* The wrapper must carry `_pipecat_cleanup`, or the MCP connection stays open after teardown.
* `MCPClient` drops the MCP annotations when it converts schemas. They are only on `session.list_tools()`, as `read_only_hint` and so on with SDK 2.x. This needs the private `_active_session`. The Pipecat pin is exact, and a test fails if it stops working.
* `_pipecat_timeout_secs` starts when the handler starts, so it would count an undo wait held inside the handler.

| Arm | Design | Result |
| --- | --- | --- |
| A | Pipecat async tool (`cancel_on_interruption=False`), open for the whole wait | Commits and survives the interruption. Adds a `developer` role message per update and about 100 tokens of `ASYNC TOOLS` text to every request. |
| B | Handler returns `waiting_for_undo` at once, harness owns the wait, outcome replaces the tool message | Commits and survives the interruption. Plain tool result, no new role, system prompt unchanged. |
| S | Ordinary sync call with the MCP call shielded | The write finishes after the interruption, but the context says `CANCELLED`, which is false. |

## Decision

* Use arm B for held actions, and arm S for ordinary writes, with the real result written over the `CANCELLED`.
* `harness/tool_policy.py` applies the wrapper. The policy enforces `timeout_s` itself, around the tool call only.
* The LLM never decides whether a held action runs. It gets `waiting_for_undo` or `needs_confirmation` and is told not to call again. Only a transcript moves a held action, through `ToolPolicy.on_transcript`.
* `PolicyGate` sits before the `DuckingProcessor`, because that drops short finals as backchannels and `immediate` mode has no listener at all. Stopping acts on interim text. A yes needs a final transcript of at most `yes_max_words`, so "okay, but what time is it" approves nothing. Any other final reply to a confirmation counts as no.
* An answer of up to `answer_max_words` is consumed and answered with a fixed phrase, so "cancel" costs no LLM call. A longer one acts on the hold and also reaches the LLM.
* `llm.register_function(None, policy.deny_unknown)` catches a model that calls a removed or invented tool, so those denials are logged and reach no server.
* Held actions are dropped at the end of a live session. Scripted runs wait for undo windows first, so a run never ends with the bot having promised something that did not happen.
* A success after the window is silent, since the user was already told. A failure is spoken.

`list_timers` now returns `{"timers": [...]}`. An empty bare list reached `MCPClient` as no content, which it reported to the LLM as a failed call.

## Alternatives

* Arm A. Rejected: the `developer` role is untested on Groq, and testing it costs money, while B needs nothing new from any provider.
* Subclass `MCPClient`. Rejected: it would override more private methods than the one read used here.
* Disable interruptions during tools. Already rejected in ADR-009.

## Consequences

* The undo and confirm waits are outside the path to the first reply, so latency reports need no correction for them, which settles that point from ADR-009.
* The tool message in the context changes after the LLM has seen it. This is deliberate: the LLM always sees the current truth.
* The undo window starts when the tool returns, so the bot's own sentence uses part of the 5 s.
* "cancel my other timer" during a window also undoes the held action. That is the safe direction.
* The status is not called `pending`, because a successful `set_timer` already returns `status: pending` for a timer that is counting down. The runner caught this.
* Not verified live: a timeout on a real slow MCP call (tested with a stand-in handler), and Flux interim transcripts through `PolicyGate`.

## Live check, Oct 31

`bench/task_runner.py` ran once on Groq `gpt-oss-20b`, with fake STT and TTS and each case's own server only. Single runs, so these are not pass rates. Cost $0.0019 and $0.0022.

* First run, 16 of 24. `waiting_for_undo` and `needs_confirmation` were both respected. Four failures were real: the model asked the user for timer and note ids. Four were cases fitted to the fake LLM's wording.
* After telling the model to find ids itself and loosening those cases, 23 of 24. Every undo, commit, confirm, and decline case behaved. The one miss is `notes_delete_runs_after_undo`, which reached the right result with one extra `list_notes` call. It stays a failure because the extra call is a real LLM round trip.
* In the runner the window is 0.2 s or 3 s, so the 5 s window itself was not run live.

## Sources

* Installed package: `pipecat/services/mcp_service.py`, `pipecat/services/llm_service.py`, `pipecat/processors/aggregators/llm_response_universal.py`, `pipecat/processors/aggregators/async_tool_messages.py` (1.13.0)
* https://reference-server.pipecat.ai/en/latest/api/pipecat.services.mcp_service.html
