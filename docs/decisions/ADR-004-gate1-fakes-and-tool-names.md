# ADR-004: Gate 1 runs on fakes first; clock tool names; Aura-2 before Cartesia

Date: Oct 21, 2026. Status: accepted.

## Context

Working rule 2 says every provider defaults to a fake and real providers are switched on one at a time after approval. Gate 1 asks for a spoken request. The prompt's step 2 names Cartesia for TTS, while the review's budget reserves Cartesia's Pro credit for truncation tests and final runs. The review names the clock tools `set_timer`, `set_alarm`, `list`, and `cancel`.

## Decision

1. **Gate 1 is closed in two passes.** The scripted pass uses fakes for STT, LLM, and TTS, but real Silero VAD and Smart Turn v3, driven by speech synthesised with macOS `say` and streamed in real time (`--input wav`). It runs over stdio and streamable HTTP as `tests/test_gate1.py`. The live pass (a person speaking, real Flux, Groq, and TTS) waits for keys and approval.
2. **TTS for the live Gate 1 is Deepgram Aura-2** on the free credit. Cartesia comes in at step 4, the interruption test, which is where word timestamps matter.
3. **Clock tools are `set_timer`, `set_alarm`, `list_timers`, `cancel_timer`.** Pipecat merges tools from every MCP server into one flat list for the LLM, so bare `list` and `cancel` would collide once `notes` or `delegate` are loaded.
4. **Caps are in USD at list price**, so free-tier usage still counts and a free tier needs a small nonzero cap to be used.

## Alternatives

* Wait for keys before testing anything. Rejected: it would leave the gate untested until Oct 23.
* Cartesia from day one. Rejected: it spends the $5 Pro credit on bulk development.

## Consequences

The scripted pass proves the MCP path, the turn logic, and the logging without spending money. It does not prove ASR accuracy or real LLM tool choice; the live pass does.

## Sources

* docs/prompt.md, Working rules 2 and 7; First spike steps 2 and 3
* docs/architecture.md, "Proposed spend" and "Task servers for the ME2 commands"
