# ADR-009: Waiting phrase, layered task tests, and per-tool policy

Date: Oct 31, 2026. Status: accepted by Quiel on Oct 31, 2026. No code is written yet.

## Context

A review of the ElevenAgents docs found three controls worth adapting to ME3: a soft timeout that speaks a filler, layered agent tests with pass rates and failure buckets, and per-tool approval settings. The core structure (ASR, LLM, TTS, turn taking) already matches ours, so none of this changes the pipeline.

Each one closes a gap we already have:

* Live tool turns take 2.8 to 3.0 s to first TTS byte (ADR-005), and the 1.0 s budget in the review has no line for the second LLM call.
* `bench/task_suite/` is empty, and the model comparison has no runner.
* The review requires a harness-enforced tool policy, but tools currently go straight from `MCPClient` to the LLM.

Checked against installed Pipecat 1.13.0: `LLMService` has the `on_function_calls_started` event and `function_call_timeout_secs`, per-function timeouts and `cancel_on_interruption` exist, and `MCPClient.tools()` returns a `ToolsSchema` whose handlers register as `_tool_wrapper_with_cleanup`. There is no documented policy hook, so wrapping that handler needs a spike.

## Decision

### 1. Waiting phrase

* A short local clip ("One sec") plays while the agent is working. It uses the pre-synthesised phrase cache the review already plans for network loss, so that cache gets built once.
* It triggers when a tool call starts, or when no reply audio has started `filler.after_ms` after end of turn (600 ms is a hypothesis to tune).
* It plays at most once per turn, is skipped if reply speech has already started, and stops on any interruption.
* It never enters the LLM context or the `played_up_to` word count.
* `stay_silent` never triggers it. The timer trigger runs only in ACTIVE, so the agent cannot say "one sec" to a side conversation.
* Until `voice-speak` is real, a small processor in the harness plays it. Later it moves to the speak server.
* It is off by default in both profiles until tested.
* **It does not count toward the latency target.** The event log gets a separate `filler_audio_out` event, and the headline stays end of speech to first answer audio.

### 2. Layered task tests

Cases live in `bench/task_suite/cases/*.yaml`, and `bench/task_runner.py` runs them. It reuses `run_bot` text mode.

| Layer | Checks | Cost |
| --- | --- | --- |
| Next reply | One turn: right tool, no tool, or `stay_silent` | LLM only |
| Tool call | Arguments by exact match or regex, plus final state on the mock server | LLM only |
| Conversation | Scripted multi-turn user, such as set, list, cancel | LLM only |
| Audio timing | WAV clips through VAD: stop latency, heard versus committed words, phrase ordering. Extends `bench/interruption_test.py` | STT and TTS when run live |

* `--repeat N` reports a pass rate, not a single pass or fail. The bars stay those in the review's decision rule.
* Failures fall into fixed buckets: `wrong_tool`, `missed_tool`, `bad_args`, `wrong_state`, `spoke_when_silent`, `timeout`, `provider_error`. Plain code assigns them, with no LLM judge, so the suite adds no evaluator spend.
* The runner with fakes is part of `uv run pytest -q`. Live runs need `ME3_LIVE=1`, and all spend goes through the cost ledger.
* Reports include cost per successful task.

### 3. Per-tool policy

`harness/tool_policy.py` sits between `MCPClient` and the LLM, with overrides in `configs/tool_policy.yaml`.

| MCP hints | Default |
| --- | --- |
| Read only | Run at once |
| Writes, not destructive | Run at once |
| Destructive, closed world (`cancel_timer`) | Run after the 5 s undo window |
| Destructive and open world (calls, messages, purchases) | Spoken confirmation, then the undo window |
| Hints missing | Spoken confirmation |

* Each tool has `permission` (`auto`, `confirm`, `disabled`), `undo_s`, and `timeout_s`.
* Disabled tools are removed from the schema, so the LLM never sees them and the prompt stays small.
* Startup fails if two servers share a tool name, since Pipecat flattens them.
* Destructive tools still default to mocks.
* The microphone is never blocked during a tool. ME3 is full duplex, so we do not copy the option to disable interruptions. Only the commit step is protected: once the undo window ends, an interruption no longer cancels the action.
* "Cancel" during an undo window must work in both barge-in modes. In `immediate` mode the allowlist does not run today, so the policy needs its own transcript check.
* Every decision is written to the event log, including denials the servers never see.

## Alternatives

* Copy the visual workflow builder. Rejected: a small rule for model and tool selection is enough. Per-stage model choice stays with the `LLMSwitcher` work.
* Use an LLM evaluator for every test. Rejected: it adds cost and noise when gold answers exist.
* Disable interruptions during tools. Rejected: it conflicts with full duplex and with spoken "cancel".
* Use a generated filler. Rejected for now: it adds a TTS and LLM call at the moment we are trying to save time.

## Consequences

* The first model comparison can run on Groq alone, with no new purchase. `configs/providers.yaml` holds one Groq price, so `gpt-oss-120b` needs its own price entry in the ledger first.
* Each ME2 command needs a mock or real server before its test cases can run, so the runner grows with the task servers.
* The undo window delays destructive tools by 5 s on purpose. Latency reports must exclude that wait.
* A filler can annoy if it plays on nearly every turn, so its rate is logged and reviewed.
* Clips are synthetic, not recordings of people, and must be regenerated when the voice changes.

## Sources

* https://elevenlabs.io/docs/eleven-agents/overview.md
* https://elevenlabs.io/docs/eleven-agents/customization/conversation-flow.md
* https://elevenlabs.io/docs/eleven-agents/customization/agent-testing.md
* https://elevenlabs.io/docs/eleven-agents/customization/tools/mcp.md
* https://elevenlabs.io/docs/eleven-agents/customization/tools/tool-configuration/tool-interruptions.md
