# ADR-046: Eager end of turn on, and shorter spoken replies

Date: Nov 6, 2026. Status: accepted (Quiel asked for both on Nov 6) and measured the same day. Changes ADR-018 on eager.

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
- Measured on Nov 6 after the merge, with fake TTS so no Cartesia credits (Results, below).

## Results (Nov 6, after the merge)

All three checks used live Flux and Haiku 5.5 with a fake TTS, on the MacBook Air, for $0.12 in total.

**Eager off against 0.5**, `bench.eot_sweep --stt deepgram_flux --llm anthropic --grid eager_eot_threshold=off,0.5
--repeat 3` (rows in `logs/eot_sweep-20261106-163022.jsonl`, $0.065). The same 12 synthetic `say` clips per arm, 3
repeats. Reply is from the last loud sample to the first TTS byte, p50, so compare the arms, not the numbers with
headline latency.

| Clips | Eager off | Eager 0.5 | Saved | Cut into the pause |
|---|---|---|---|---|
| Command (tool) | 3,258 ms | 2,903 ms | 355 ms | |
| Chat (no tool) | 2,334 ms | 2,175 ms | 159 ms | |
| Pause mid-sentence | 3,402 ms | 2,378 ms | 1,024 ms | 6 of 12 in both arms |

- Chat turns save 159 ms, close to ADR-018's 151 ms on Groq, and under that ADR's 200 ms bar. Haiku's slower first
  token did not make the head start worth more on these clips.
- Tool turns save too: when Flux confirms the turn before Haiku reaches the tool call, Pipecat releases the
  speculation and the call runs as an ordinary one.
- Eager added no cut-ins: the pause clips were cut 6 times in 12 with it off as well.
- LLM calls rose from 63 to 77 (1.22 times), with 14 of 54 predictions withdrawn, all on the pause clips. The cost was
  about the same ($0.032 off, $0.033 on).

**End to end with the new defaults**, `bench.latency_bench --stages
stt=deepgram_flux,llm=anthropic,tts=fake_words_fast --sessions 3 --turns 8 --all-servers` (report
`logs/latency-20261106-165058.json`, $0.034, 15 turns measured): from the end of speech to first reply audio, p50
2,352 ms overall, 1,891 ms without a tool and 2,829 ms with one. Haiku's first token p50 was 673 ms, and the end of
turn came 1,105 ms after speech. The fake TTS answers in 139 ms where Cartesia took about 300 ms today, so real speech
adds about 0.16 s. These are synthetic clips with a longer end-of-turn wait than the class benchmark's, so there is no
like-for-like baseline: the sweep above is the controlled comparison. Output tokens per call had a median of 26
against 59 on the class-benchmark run with the old prompt, on different requests, so this is a hint, not a result.

**Task suite**, `bench.task_runner --stages llm=anthropic --all-servers`: 66 of 71 passed (93%), $0.022, against 61
of 64 (95%) on Nov 1 with the older prompt and a smaller suite (ADR-025). The five failures:

- `addr_side_talk_stays_silent` and `addr_side_talk_about_a_person`: the same two as on Nov 1. They matter only with
  addressing on, which is off.
- `next_time`: she said "four fifty-two in the afternoon", and the case wanted a digit. The prompt asks for numbers
  as words, so the case now accepts number words.
- `delegate_start_runs_at_once`: the goal began "Tidy up", and the case's pattern was case-sensitive. It is not now.
- `workspace_plan_and_build_a_larger_site`: she handed the website to `start_task` (the background worker, ADR-035)
  instead of building it with `write_file`. Not resolved: the delegate tool came after the Nov 1 run, so this may be
  the tool and not the prompt. A few repeats with each prompt would tell.

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
