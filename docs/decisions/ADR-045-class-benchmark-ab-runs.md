# ADR-045: A/B runs on the class benchmark, and prompt caching on

Date: Nov 6, 2026. Status: accepted (Quiel chose the four changes to try and approved caching on, Nov 6).

## Context

The five-round class-benchmark run (`docs/class-benchmark.md`) showed two weak points: she answers speech not meant for
her (false turn-taking 8 of 10), and she is slow to answer (about 2.35 s from the end of the user's turn to her first
audio). Four changes from `TODO.md` might help, and Quiel asked to try all four: Anthropic prompt caching, addressing
(ADR-016), duck-then-decide barge-in (ADR-007), and Flux eager end of turn.

Pipecat 1.13 has `AnthropicLLMSettings.enable_prompt_caching`, off by default. With caching on, Anthropic reports
`prompt_tokens` net of the cache and gives `cache_creation_input_tokens` and `cache_read_input_tokens` beside it, so the
ledger would undercount without new price rows. Haiku 5.5 prices (Anthropic prompt caching page, checked Nov 6):
input $0.10 per million, 5 minute cache write $0.125, cache read $0.01, output $0.50. The minimum cacheable prompt is
512 tokens; ours (system prompt and tool schemas) is well above that.

## Decision

- `bench/fdb_adapter.py` takes A/B switches from the environment, so a run needs no project edit: `ME3_PROMPT_CACHE`
  (0 or 1), `ME3_ADDRESSING=1` (the level gate stays off, since it is unverified with Flux), `ME3_BARGE_IN`
  (immediate or duck), and `ME3_FLUX` (Flux settings as `k=v` pairs). Bad values refuse. `run_start` in the event log
  records barge-in mode, addressing, caching and the Flux settings, and `last_run.txt` records the switches.
- `harness/providers.py` passes `prompt_caching` from `providers.yaml` to the Anthropic service, and
  `harness/costs.py` prices cache writes and reads at the rates above.
- **Prompt caching is on by default** (`providers.anthropic.prompt_caching: true`). It cuts the LLM cost by about 87%
  and changes nothing else measurable.
- Addressing, duck mode and Flux settings stay as they were (off, off, ADR-018).
- `bench/fdb_judge.py` runs fdbench's judge (GPT-4o through OpenRouter) inside the cost ledger: it checks the cap,
  reads the key's usage from OpenRouter's key endpoint before and after, and records the difference.

## Results

Each variant ran 3 rounds with seed 174374, the same 30 clips as rounds 1 to 3 of the baseline (`2026-11-06_10-28-03`,
ME3 run `20261106-102804-7e95d7`). One trial out of 3 is within run-to-run noise.

| Variant | fdbench run, ME3 run | Spend | Verdicts changed vs baseline | Reply latency p50 | LLM first token p50 | Stop on interruption, mean |
|---|---|---|---|---|---|---|
| Baseline (5 rounds) | `2026-11-06_10-28-03`, `20261106-102804-7e95d7` | $1.08 | | 2.35 s | 972 ms | 0.67 s |
| Prompt caching | `2026-11-06_14-47-52`, `20261106-144753-396945` | $0.60 | +1 backchannel, -1 noise | 2.40 s | 1,009 ms | 0.64 s |
| Addressing | `2026-11-06_15-04-48`, `20261106-150449-2613af` | $0.64 | +1 backchannel, -1 Candor pause | 2.38 s | 978 ms | 0.67 s |
| Duck mode | `2026-11-06_15-21-10`, `20261106-152112-8f46b9` | $0.68 | -1 backchannel clean twin | 2.37 s | 1,037 ms | 0.86 s |
| Flux eager 0.5 | not run | | | | | |

- **Caching**: 479,233 tokens read from the cache, 10,879 written, 214 uncached. Anthropic spend was $0.0079 against
  $0.061 for the same three rounds of the baseline. No latency gain: a short prompt was never the slow part.
- **Addressing**: false turn-taking stayed at 0 of 5 correct. She stays ACTIVE for 30 s after she speaks, and the
  trials come less than 30 s apart, so the IDLE rules never applied. `stay_silent` was called 3 times. This says the
  benchmark cannot test addressing as built, not that addressing fails in a room.
- **Duck**: she stops later, because the benchmark counts ducked (quieter) speech as still talking until the decision.
  Stop latency rose by about 0.2 s, which is the wrong way for every v1.5 scenario.
- **Eager end of turn**: cancelled before it ran, to keep Cartesia credits for the demo (Quiel, Nov 6). The A/B runs
  used about 9,500 Cartesia characters each (1 credit per character on Quiel's plan).

Where the time goes in every variant (p50): Flux end-of-turn wait 0.8 s, Haiku first token 1.0 s, Cartesia first
audio 0.3 s. None of the four changes touches these. Shorter replies would cut TTS cost most, and eager end of turn
is the untested latency lever.

## Alternatives

- Keep caching off: no reason, since it is cheaper and no slower.
- Turn addressing on anyway: unsupported by this evidence, and it has only run on fakes (ADR-016).
- Shorten the ACTIVE window so the benchmark reaches IDLE: possible, but it would tune for the benchmark's spacing,
  not a room. Not done.
- Run more rounds per variant: limited by Cartesia credits.

## Consequences

- Live Anthropic runs now write a 5 minute cache on the first turn (1.25x input) and read it afterwards (0.1x). The
  ledger counts both. A session idle longer than 5 minutes pays one more write.
- The A/B switches stay in the adapter for later runs, such as eager end of turn once credits allow.

## Sources

- Anthropic prompt caching, prices and minimums: https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- Pipecat 1.13 `AnthropicLLMService.Settings.enable_prompt_caching`, read from the installed package
- fd-benchmark: https://github.com/trueshotjames/ai231-me3-full-duplex-benchmarking
