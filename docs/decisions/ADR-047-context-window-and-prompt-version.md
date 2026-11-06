# ADR-047: Keep the last 20 user turns, and name and version the prompt

Date: Nov 6, 2026. Status: accepted (Quiel chose both on Nov 6 while filling the machine exercise template). Built and tested on fakes only.

## Context

The machine exercise template asks for two things we did not have.

- **Section 3, context engineering:** a token budget with trimming or summarizing. Our LLM context grew for the whole session. Each Haiku call already sends about 8,400 prompt tokens (TODO.md), most of it the system prompt and the tool definitions, and every turn added more history on top.
- **Section 2, prompt:** a named persona and a prompt version kept in the repo. The prompt was an unversioned constant in `harness/bot.py`, and the agent had no name.

The updated class benchmark (fd-benchmark 9d7a824, Nov 6) now logs the context size of every LLM call, so the effect of a limit can be shown on the slide.

## Decision

**Context window.** `harness/context_window.py` (`ContextWindow`) sits between the user aggregator and the LLM. On each `LLMContextFrame` going down, it counts the plain user messages, and when there are more than `context.keep_turns` (20 in both `mac.yaml` and `pi.yaml`), it drops everything before the oldest one it keeps. It edits the stored context in place, so the history really shrinks, and logs `context_trimmed` with the counts.

- A cut is always made just before a user message, so a tool call is never split from its result, and the kept history starts with a user message, as Anthropic requires.
- The system prompt lives on the LLM service and the tools in `context.tools`, so neither is ever dropped.
- A tool call still waiting for its result comes after the last user message, so it is always kept. Context frames that come back up from the assistant aggregator after a tool result never pass this processor, so the history is never cut in the middle of a tool loop.
- `keep_turns: 0` keeps everything, the old behaviour.

**Prompt name and version.** The generalist prompt now opens "You are VIA, short for voice input agent". Quiel's pronunciation is "vee-yah". `PROMPT_VERSION` in `harness/bot.py` starts at `v1`, and `run_start` logs it with `keep_turns`. Bump it whenever `SYSTEM_PROMPT` or `agents.DOCUMENT_PROMPT` changes. The class-benchmark entry's new `describe()` sends it to fdbench's results card with the model names. Document agents keep their own names.

## Alternatives

- **A token budget (for example 16K tokens of history).** Closer to the template's wording, but it needs a token estimate per message before the call, and the count of turns gives the same bound for spoken turns, which are short.
- **Summarize old turns with the LLM.** One more paid call each time, and more to test, the day before the deadline.
- **Trim in steps (for example to 15 when it reaches 25).** Fewer changes to the start of the history, so fewer cache misses on it, but the slide would then say "15 to 25 turns". Quiel chose a plain 20.
- **No limit, described as a limitation.** Offered, not chosen.

## Consequences

- With the default of 20, a demo session of a few minutes is never trimmed, so its behaviour is unchanged. Trimming starts on turn 21 of a long session.
- Prompt caching: tools and the system prompt stay cached, because the cache marker sits on the system prompt and they come first in the request. Pipecat 1.13 also marks the two latest user messages. Once trimming starts, the start of the history changes every turn, so the cached history from the previous turn no longer matches and is written again. The history at 20 spoken turns is small next to the 8,400 token prefix, so the cost of this is small, but it has not been measured.
- The model forgets anything said more than 20 turns ago. Tools still hold the real state (timers, notes, lights), so it can look it up again.
- Renaming the prompt changes the cached prefix once, so the first call after the change writes the cache again.
- Not checked yet: how Cartesia says "VIA". If it reads it as letters or as "vye-uh", spell it differently in the prompt and bump the version.

## Sources

- Pipecat 1.13.0, installed: `pipecat/adapters/services/anthropic_adapter.py` (`_system_with_cache_control`, `_with_cache_control_markers` marks the two latest user messages), `pipecat/processors/aggregators/llm_context.py` (`set_messages` replaces the list in place).
- Anthropic prompt caching, cache prefix order tools, then system, then messages: https://docs.claude.com/en/docs/build-with-claude/prompt-caching
- fd-benchmark context size and model versions: https://github.com/trueshotjames/ai231-me3-full-duplex-benchmarking/blob/main/docs/STAGES.md
