# ADR-041: Drop a spoken waiting phrase before a tool call

Date: Nov 5, 2026. Status: accepted (Quiel approved the fix on Nov 5).

## Context

The prompts tell the model to call a tool straight away with no words before it, because the device plays its own waiting clip (ADR-033). In the Nov 4 recheck of the Coach agent, DeepSeek V4.1 Flash on OpenRouter said "I'll check your documents" before its tool call anyway. Spoken, it doubles up with the clip, and a prompt rule alone does not hold for every model an agent can now pick (ADR-040).

## Decision

`harness/preamble.py` (`PreambleFilter`) sits between the LLM and the TTS. When a reply's first words could begin a waiting phrase (a short list: "let me", "I'll check", "one moment", and the like), it holds them until the first sentence ends. A tool call in the same response drops the held sentence and logs `preamble_dropped`. More text, or the end of the response with no tool call, releases it unchanged. Any other reply passes at once.

Pipecat announces a call with `FunctionCallsStartedFrame`, a system frame that can overtake queued text, so the filter remembers the call for the rest of that response.

## Alternatives

- **Prompt only:** already in place, and DeepSeek ignored it.
- **Hold every reply until its response ends:** catches any wording but adds the whole generation time to every answer, which the latency headline cannot afford.
- **Strip text from any response that has a tool call:** a model that explains a refusal and then calls a tool would lose words it meant to say.

## Consequences

- An ordinary reply is not delayed. A reply that starts like a waiting phrase but has no tool call waits until its first sentence ends and the next text arrives, a few tens of milliseconds.
- A waiting phrase worded outside the list still gets through. `preamble_dropped` counts the ones caught, so a live run shows whether the list needs more.
- Checked on fakes only, then live on Nov 5 (`20261105-195758-061828`): Haiku 5.5 began two tool replies with a waiting phrase despite the prompt ("I'll look that up for you.", "Let me check what's going on with the music player."). Both were dropped and not spoken. DeepSeek V4.1 Flash said none in its two turns.

## Sources

- Pipecat 1.13.0, installed: `pipecat/services/llm_service.py` (`run_function_calls` broadcasts `FunctionCallsStartedFrame`), `pipecat/services/anthropic/llm.py` and `pipecat/services/openai/base_llm.py` (tool calls run before `LLMFullResponseEndFrame`).
- ADR-033 (waiting phrase), ADR-040 (OpenRouter models).
