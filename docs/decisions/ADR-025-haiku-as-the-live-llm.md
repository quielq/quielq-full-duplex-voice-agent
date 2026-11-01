# ADR-025: Claude Haiku 5.5 as the live LLM

Date: Nov 1, 2026. Status: approved by Quiel, Nov 1, 2026, subject to the run below. Replaces the choice left open in ADR-017.

## Context

Groq's limits page lists 8,000 tokens a minute and 200,000 a day for `gpt-oss`, as the Developer plan's base limits, with more only for select workloads. An ME3 call is about 5,000 prompt tokens with all servers on, so the per-minute limit allows about one and a half calls. In ADR-017, 86 runs were lost to the daily limit. Haiku 5.5 was already wired in `harness/providers.py`.

## Decision

Use `llm=anthropic` with `claude-haiku-5-5` for live runs and the task suite. The `llm` stage still defaults to fake. Groq stays available.

Pipecat 1.13 sends `thinking: disabled` for this model when neither `thinking` nor `effort` is set, so no setting was added.

## Results

`bench.task_runner --stages llm=anthropic --all-servers`, Nov 1, 2026, on the laptop, one repeat, 37 tools offered:

| | gpt-oss-20b (ADR-017) | gpt-oss-120b (ADR-017) | Haiku 5.5 |
| --- | --- | --- | --- |
| Passed | 142 of 160 (88.8%) | 194 of 205 (94.6%) | 61 of 64 (95.3%) |
| Runs lost to a rate limit | 86 | 0 | 0 |
| Time to first token, p50 | 902 ms | 2,554 ms | 701 ms |
| Time to first token, p95 | 7,238 ms | 5,994 ms | 909 ms |
| Cost per passed case | $0.00020 | $0.00039 | $0.00133 |

The whole run cost $0.08. Time to first token is from 58 requests, between `llm_request` and `llm_first_token`. It misses ADR-017's 350 ms target and beats the 700 ms bound only at about the median.

The Groq columns come from an older suite with 18 tools and five repeats, so this is a strong signal and not a controlled comparison.

The three failures:

- `addr_side_talk_stays_silent` and `addr_side_talk_about_a_person`: Haiku answered side talk that was not for it. Both matter only with addressing on, which is off by default (ADR-016).
- `addr_short_follow_up_in_active`: the case expects "thanks" in the reply, and is too strict (ADR-017).

## Alternatives

- **Stay on Groq.** Cheaper per case, but the limits stop benchmarks and long sessions.
- **Sonnet 5.5.** Costs 20 times as much per token, with no need shown yet.
- **Prompt caching.** Available in Pipecat, not yet tried. It may cut cost and time to first token, because the tool schemas repeat on every call.

## Consequences

- Cost per case is about 3.5 to 7 times Groq's. A voice turn is still a fraction of a cent.
- Side talk needs work before addressing can be turned on with Haiku.
- A single repeat cannot show flakiness, so the 95.3% has a wide margin.
- New Anthropic accounts may start on a lower Evaluation tier, so check the Console before a long run.
- This run used fake tools and text input. A live voice turn on Haiku with the read-back is still to do.

## Sources

- https://console.groq.com/docs/rate-limits
- https://platform.claude.com/docs/en/api/rate-limits
- Pipecat 1.13.0, installed package: `pipecat/services/anthropic/llm.py`
