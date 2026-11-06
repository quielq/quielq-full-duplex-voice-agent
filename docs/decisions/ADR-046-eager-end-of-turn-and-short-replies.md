# ADR-046: Eager end of turn on, and shorter spoken replies

Date: Nov 6, 2026. Status: accepted (Quiel asked for both on Nov 6, to be measured later). Changes ADR-018 on eager.

## Context

After the class-benchmark A/B runs (ADR-045), the reply still takes about 2.35 s at the median from the end of the
user's speech to her first audio: about 0.85 s for Flux to commit the end of turn, about 1.0 s to Haiku's first token,
and about 0.5 s for the first sentence and Cartesia's first audio. Cartesia is now the main running cost: Quiel's plan
is 100,000 credits a month at about one credit per character, and the benchmark replies averaged about 300 characters
a trial. With prompt caching (ADR-045), the LLM input is cheap: a 3-round benchmark run cost $0.008 on Anthropic.

ADR-018 (Nov 1) kept eager end of turn off: on Groq `gpt-oss-20b` it saved 151 ms on questions, under its 200 ms bar.
Haiku's first token is slower than that model's (about 700 ms against 570 ms), so the head start is worth more now.
That is a reason to expect a larger saving, not a measurement.

The installed Pipecat 1.13 holds a speculative reply in the LLM service's `SpeculationGate` until the turn is
confirmed, and discards it if Flux withdraws the guess, so no withdrawn text reaches the TTS. A speculative reply that
wants a tool call is withdrawn instead, and the tool runs only after the committed turn.

## Decision

- `providers.deepgram.flux.eager_eot_threshold` is 0.5, which turns eager end of turn on. `--flux
  eager_eot_threshold=off` (or `ME3_FLUX` on the class benchmark) turns it off again.
- The system prompt asks for a short first sentence of a few words and a whole reply under about 30 words, and the
  document agents' prompt asks the same. The first sentence is what the sentence aggregator waits for before the first
  TTS request, so a shorter one starts audio sooner. `TextAggregationMode.SENTENCE` stays.
- The generalist prompt is rewritten shorter (381 words to 317) with every rule kept.

## Consequences

- Each withdrawn guess costs one extra LLM call. On ADR-018's pause clips most guesses were withdrawn (17 of 21), so
  calls can rise by up to about half. With caching, an extra call costs about $0.0001 in input. The ledger already
  meters withdrawn calls.
- Tool turns gain nothing from eager, since speculation stops at a tool call.
- Eager may cut into a mid-sentence pause less safely than the committed end of turn. Only the held reply is at risk,
  and it is discarded if she keeps talking, but pause handling should be checked when it is measured.
- The prompt saves about 80 tokens a call out of about 6,700 on the first call, most of which is the tool list. The
  real saving is in output and Cartesia characters, if replies get shorter.
- Not measured yet. The check is `bench.latency_bench` with Flux and Haiku and fake word-timed TTS (no Cartesia
  credits), against the ADR-045 baseline, and a task-suite pass on Haiku to see that shorter replies still answer.

## Alternatives

- Keep eager off until it is measured: Quiel chose to turn it on now and measure later.
- Lower `max_tokens`: it would cut long replies but also cut a `write_file` call mid-file (ADR-028), so it stays 8192.
- Trim the tool list: the bigger input saving, but input is cheap with caching, and fewer tools means fewer things
  she can do.

## Sources

- Pipecat 1.13 installed source: `pipecat/turns/speculation_gate.py`, `pipecat/services/llm_service.py` (speculative
  tool calls), `pipecat/services/deepgram/flux/stt.py` (`enable_eager_end_of_turn`)
- Deepgram Flux end-of-turn settings: https://developers.deepgram.com/docs/flux/configuration
- ADR-018, ADR-045
