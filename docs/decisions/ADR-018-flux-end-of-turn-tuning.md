# ADR-018: Flux end-of-turn tuning, keep the defaults

Date: Nov 1, 2026. Status: accepted, no default changed.

## Context

End of turn was the largest block in the 2.8 s loop (ADR-005). We tested `eot_threshold`, `eot_timeout_ms`, and eager end of turn with `bench/eot_sweep.py`: 12 `say` clips (4 commands, 4 questions, 4 with a 0.9 s or 1.2 s pause mid-sentence), live Flux, laptop, one morning. The rules were fixed before the runs:

- `eot_threshold`: the lowest value that cuts the pause clips no more often than 0.7.
- Eager: on only if it saves at least 200 ms p50 on non-tool turns, adds no more than 100 ms on tool turns, and keeps LLM calls under 1.5 times the baseline.

## Results

Threshold sweep: fake LLM and TTS, 3 repeats. Wait is from the last loud sample to `end_of_turn`, p50.

| `eot_threshold` | Commands | Questions | Pause clips cut, of 12 |
| --- | --- | --- | --- |
| 0.5 | 1103 ms | 1002 ms | 12 |
| 0.6 | 1123 ms | 998 ms | 12 |
| 0.7 (default) | 1258 ms | 1039 ms | 6 |
| 0.8 | 1372 ms | 1351 ms | 3 |

The first version of the scorer missed runs that stopped on a fragment such as "Set a timer for". The cut counts above were corrected by hand from the rows, adding 1 each at 0.5, 0.6, and 0.7. The scorer now counts these. Every one of the 174 finals had `trigger: model`.

`eot_timeout_ms=2000`, 2 repeats: no turn ended on the timeout, and 4 of 8 pause clips were cut, in line with 0.7. These clips never came near the 7 s case, so this says nothing about whether a timeout would cap it.

Eager trial: Flux, Groq `gpt-oss-20b`, fake TTS, all servers, 2 repeats, `eot_threshold` at the default. Reply is from the last loud sample to the first TTS byte, p50.

| `eager_eot_threshold` | Commands | Questions | Eager predictions | Withdrawn | LLM calls |
| --- | --- | --- | --- | --- | --- |
| off | 2776 ms | 1845 ms | 0 | 0 | 47 |
| 0.4 | 2161 ms | 1694 ms | 39 | 20 | 68 (1.45x) |
| 0.5 | 2760 ms | 1694 ms | 34 | 13 | 60 (1.28x) |

Total spend for all runs, about $0.15 ($0.13 Deepgram, $0.015 Groq).

## Decision

Keep `eot_threshold` at 0.7, `eot_timeout_ms` at 5000, and eager end of turn off. All three stay unset in `configs/providers.yaml`.

- Below 0.7, cuts double and the wait barely moves: 135 ms on commands and 41 ms on questions.
- 0.8 halves the cuts but adds 114 ms on commands and 312 ms on questions.
- Eager saves 151 ms on questions at both thresholds, under the 200 ms bar. The 615 ms saving on commands at 0.4 is not trusted: it is 8 runs, 0.5 saved 16 ms, and speculation cancels on a tool call, so it has no mechanism to help a tool turn. One 0.4 run also timed out, and most pause-clip predictions were withdrawn (17 of 21).

## Alternatives

- Lower the threshold anyway for a faster feel. Rejected: half of the pause clips are already cut at 0.7, and a cut turn acts on a fragment.
- Turn eager on at 0.5. Rejected by the rule. It remains an option if the LLM gets faster.
- `ForceEndTurn` with Smart Turn. Not tried. See below.

## Consequences

- The wait on complete sentences has a floor of about 1.0 s that this threshold does not move, so Flux settings cannot reach the p50 target of 1.0 s. Non-tool replies sit near 1.8 s and tool replies near 2.8 s with fake TTS.
- Flux's own pause handling is the weak point: 0.7 cuts half of the clips with a long pause. `say` pauses are clean silence after an unfinished phrase, and a person may differ, so this needs checking on the live mic.
- Deepgram documents a `ForceEndTurn` message, which ADR-005 assumed did not exist. With `eot_threshold=1.0`, a local detector can end a Flux turn. Pipecat 1.13 does not send it, but `_transport_send_json` is available. This is the next lever for the 1.0 s floor, and needs a spike (done in ADR-019, not adopted).
- Withdrawn LLM calls report no usage, because Groq sends it in the last chunk. `CostObserver` now records an estimate of the prompt tokens (`llm_input_tokens_est`), which can count a call twice and errs high.
- The gap after a tool call is untouched, and it is about 1 s of the difference between tool and non-tool turns.

These are framework timings on synthetic speech from one network at one time of day. They compare settings and are not headline latency. `gpt-oss-120b` was not tried, and it would change the eager sums because its first token is slower.

## Sources

- https://developers.deepgram.com/docs/flux/configuration.md
- https://developers.deepgram.com/docs/flux/own-turn-detection.md
- Installed source: `pipecat/services/llm_service.py` (speculative tool calls), `pipecat/turns/user_stop/eager_user_turn_stop_strategy.py`, `pipecat/services/openai/base_llm.py` (usage in the last chunk)
- Rows in `logs/eot_sweep-20261101-120045.jsonl`, `-122345`, `-122736` (local only, not committed)
