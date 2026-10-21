# ADR-005: With Deepgram Flux, Flux owns end of turn; Smart Turn A/B needs Nova-3

Date: Oct 21, 2026. Status: accepted.

## Context

The review plans to A/B Smart Turn v3 against Flux's built-in end of turn. In the first live run (Flux STT, Smart Turn stop strategy, Groq, Aura-2):

* Smart Turn could not end the turn early. Flux sends its final transcript only at its own end of turn, and the Pipecat 1.13 Flux service has no finalize control message (only `CloseStream`), so the turn waited for Flux anyway: 1.3 s and 7.1 s from speech end to end of turn.
* In the 7.1 s run the turn logic interleaved badly. The LLM ran on the transcript, called the tool, and then never spoke the result (reply timeout).
* Pipecat's Flux service recommends its own turn strategies through service metadata, but the aggregator applies them only when the app passes no `user_turn_strategies`.

## Decision

Profile setting `turn.stop`: `auto | smart_turn | flux | speech_timeout`, default `auto`. `auto` means Flux turn detection when the STT is Deepgram Flux (the app passes no strategies, so Flux's recommendation applies) and Smart Turn v3 otherwise. Combining Flux STT with `smart_turn` or `speech_timeout` raises an error.

The Smart Turn arm of the A/B uses an STT that can finalize on local VAD stop (Deepgram Nova-3 through Pipecat's `DeepgramSTTService`), to be added with the model comparison.

## Results with Flux owning the turn (synthetic `say` audio, 2 runs)

| Stage | stdio | HTTP |
| --- | --- | --- |
| Speech end to end of turn | 1875 ms | 1231 ms |
| Clock tool call | 85 ms | 62 ms |
| Groq first token (second call, after the tool) | 388 ms | 543 ms |
| Aura-2 first byte after first token | 255 ms | 245 ms |
| Speech end to first TTS byte | 3022 ms | 2788 ms |

These are framework timings, not acoustic ones, and well above the p50 under 1.0 s target. The end of turn wait is the largest block. Levers to try: Flux `eot_threshold`, eager end of turn (`EagerUserTurnStrategies`), and the Nova-3 plus Smart Turn arm.

## Alternatives

* Keep Smart Turn with Flux. Rejected: no gain, and it stalled a turn.
* Use Flux as STT only and suppress its turn events. Not possible without a finalize message.

## Consequences

On the Pi, Smart Turn only runs when the STT is not Flux. Measuring Smart Turn on a Pi core still matters for the Nova-3 arm.

## Sources

* Installed source: `pipecat/services/deepgram/flux/stt_base.py` (`service_metadata_frame`, `CloseStream`), `pipecat/processors/aggregators/llm_response_universal.py`
* Run logs `logs/events-20261021-165647-f14fd5.jsonl` (stall) and `events-20261021-165830-3f3544`, `events-20261021-165840-9fa9e4` (local only, not committed)
