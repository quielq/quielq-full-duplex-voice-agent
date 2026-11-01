# ADR-020: Reply templates in place of the second LLM call

Date: Nov 1, 2026. Status: built, off by default. The latency rule was missed by 4 ms, so the default waits for Quiel.

## Context

A tool turn makes two LLM calls. On Groq `gpt-oss-20b`, from end of turn to first TTS byte, tool turns took 1361 ms p50 and non-tool turns 737 ms. The tool took 10 ms and the second call 572 ms to first token, so the gap is that call.

`harness/tool_policy.py` can now speak a template from `configs/tool_policy.yaml` when a tool succeeds, and return the result with `run_llm=False`. Held tools, failures, and tools with no template keep the LLM.

The rules were fixed before the runs. Templates go on only if they save at least 300 ms p50 on tool turns from end of turn to first TTS byte, the suite pass rate drops by no more than one case, no compound request loses a tool call, and undo and confirmation cases all pass.

## Compound requests

Groq called the tools of a compound request one at a time in all 17 runs that made two or more calls, so skipping the second LLM call would have lost the next tool every time. Requests that sound compound keep the LLM: the words in `compound_words`, or punctuation inside the sentence. The list includes "all", "both", and "lights", because "turn off all the lights" needs one call per room.

This guard is text matching, so a compound request it does not recognize loses its second tool, silently. All 6 compound cases passed with templates on, on both models, but that is 6 sentences.

## Results

Live on `gpt-oss-120b`, because the first attempt used up the 20b daily limit of 200,000 tokens: that voided the baseline suite and all 24 latency runs, which reported the spoken error phrase. No error occurred in the 120b latency runs.

Latency: Flux, fake TTS, 4 command clips, 3 repeats per arm. Gap is end of turn to first TTS byte.

| | Templates off | Templates on |
| --- | --- | --- |
| Gap, all 12 tool turns, p50 | 1342 ms | 1046 ms |
| Gap, the 3 clips that used a template, p50 | 1350 ms | 854 ms |
| LLM calls | 24 | 15 |

"Turn off the kitchen lights" keeps the LLM because of the word "lights", so it took no template.

Suite, one pass per arm, all servers offered:

| | Templates off | Templates on |
| --- | --- | --- |
| Passed | 44 of 47 | 46 of 47 |
| Cost | $0.0179 | $0.0145 |

With templates off, one compound case missed `set_light` and one hit a model parsing error. The one failure with templates on is `addr_short_follow_up_in_active`, the case ADR-017 calls too strict. One pass is too few to credit templates with the gain, but the rule is met. On 20b the templates-on arm passed 44 of 47, with none of the failures caused by templates, and has no valid baseline.

Against the rules: 296 ms misses the 300 ms bar by 4 ms. The other three pass.

## Decision

Leave `reply_templates.enabled` false. The rule as written is not met, and the bar stays where it was set. This is a narrow miss on 12 turns, which cannot separate 296 ms from 300 ms, and it is Quiel's call whether to enable templates or remeasure.

This was also measured on 120b, which is not the configured model. The saving on 20b is unmeasured.

## Alternatives

- Remove "lights" from `compound_words`. Rejected: it trades the saving on one clip for a silent loss on "turn off the lights".
- Keep the second LLM call running and only suppress its text. Keeps compound requests safe, but costs the same and is more complex. Not built.
- A waiting phrase. It does not count toward latency (ADR-009).

## Consequences

- `_is_failure` now reads JSON text. MCP results arrive as JSON text, so a tool's `{"error": ...}` was not seen as a failure before, in the undo path too.
- A templated reply is written straight into the context, because speech outside an LLM response does not reach it here. A barge-in does not trim that sentence.
- With templates on, the model no longer checks a result before it is spoken. Templates echo the call's own arguments, so a wrong argument is spoken as if correct.
- Fixed wording will repeat. Nobody has listened to it yet.
- Cases can set `live_only` for what the fake LLM cannot do, as `compound.yaml` does.
- These are framework timings on synthetic speech. Waits moved a lot through the day: end of turn alone was 1.5 s to 1.6 s in these runs against about 1.25 s this morning.

## Sources

- Installed source: `pipecat/frames/frames.py` (`FunctionCallResultProperties`), `pipecat/services/tts_service.py` (`TTSSpeakFrame` context handling)
- Groq's 429 response in the run log
- Rows in `logs/eot_sweep-20261101-151531.jsonl` and `-135141` (voided) (local only, not committed)
