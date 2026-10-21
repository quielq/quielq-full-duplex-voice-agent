# ADR-007: Duck-then-decide prototype, where the gain lives, and what it costs

Date: Oct 21, 2026. Status: prototype, off by default (`barge_in.mode: immediate`).

## Context

The review proposes ducking the agent's audio to about 0.3 on speech onset, then within about 0.5 s either restoring it (backchannel) or interrupting, with a keyword allowlist (stop, wait, no, cancel, pause), and a duck within 100 ms of VAD onset. Pipecat has no duck mode.

## Decision

* **The gain is an `audio_out_filter` on the output transport (`DuckGain`), not a processor before the output.** TTS hands the output transport whole sentences at once, so seconds of audio sit in its queue. Pipecat runs the output filter as each frame leaves that queue, so a gain change reaches the very next write. A processor upstream of the output would only touch audio queued after the duck. This works the same with `LocalAudioTransport` on the Pi.
* **`DuckingProcessor` sits after the STT** (it sees VAD frames, transcripts, and bot speaking state). In duck mode, user-turn start strategies (and Flux, via `should_interrupt=False`) stop interrupting, and the processor interrupts with `broadcast_interruption()`.
* **Decision rule:** an allowlist word in any transcript interrupts; more than one word interrupts; speech still going after 1.0 s interrupts; a short final transcript with no command word is a backchannel: restore the volume and drop the transcript, so the user turn ends empty and the bot keeps talking.
* **Ordering workaround:** when a transcript triggers the interruption, the processor holds that transcript for 100 ms so the assistant's truncated reply is committed to the context before the user's words. Without it the history read "user: story, user: stop, assistant: partial reply". The robust fix, letting the aggregator own the turn start (`ExternalUserTurnStartStrategy` driven by the ducker), is for the barge-in work after Oct 29.
* YAML profiles quote the allowlist: a bare `no` loads as the boolean false.

## Results (fake STT and LLM, word-timestamped fake TTS, real Silero VAD and Smart Turn, `say` voices, one run each)

| Mode | Barge-in | Duck after VAD onset | Decision | Stop latency | Bot kept talking | Committed vs heard words |
| --- | --- | --- | --- | --- | --- | --- |
| immediate | "Stop right there." | none | interrupt at VAD | 266 ms | no | 9 vs 8 |
| immediate | "Mm-hmm." | none | interrupt at VAD | 248 ms | no (wrong) | 9 vs 8 |
| immediate | long new request | none | interrupt at VAD | 224 ms | no | 9 vs 8 |
| duck | "Stop right there." | 15 ms | allowlist at 961 ms | 1312 ms | no | 13 vs 11 |
| duck | "Mm-hmm." | 39 ms | backchannel at 500 ms | none | yes | full reply |
| duck | long new request | 30 ms | words at 661 ms | 957 ms | no | 11 vs 10 |

Stop latency is from the first loud sample of the barge-in to the last voiced output sample. VAD onset itself is 187 to 238 ms after speech onset with `start_secs` 0.08.

## Findings

1. **The duck target is met:** 15 to 39 ms from VAD onset to the first quieter write. From speech onset it is 218 to 253 ms, because Silero needs about 190 to 240 ms here. Lowering `start_secs` further, or a lighter onset detector for the duck only, is the lever.
2. **Backchannels survive** in duck mode and are killed in immediate mode, which is the Full-Duplex-Bench v1.5 "user backchannel" case.
3. **Real interruptions are slow in duck mode (about 1 to 1.3 s to silence)** because the fake STT only produces a transcript after VAD stop. With a streaming STT (Flux interim transcripts) "stop" should be seen mid-word, and the 1.0 s duration cap bounds the worst case. This must be re-measured with Flux before choosing a default. The local closed-vocabulary fast path (review improvement 5) is the other lever.
4. **The 300 ms stop target holds only in immediate mode** today. The trade-off is explicit: immediate mode is fast but cannot tell "mm-hmm" from "stop".

## Follow-up with live Flux interim transcripts (Oct 21, 2026, $0.01 total)

| Barge-in | Duck after VAD onset | Decision | Stop latency | Bot kept talking |
| --- | --- | --- | --- | --- |
| "Stop right there." | 23 ms | allowlist at 597 ms | 879 ms | no |
| "Mm-hmm." | 7 ms | no transcript, restored at 1478 ms | none | yes |

Flux interims cut the allowlist decision from 961 ms to 597 ms, still well above the 300 ms stop target. Flux returned no transcript at all for "mm-hmm", so the backchannel was handled by the no-transcript fallback (volume restored after about 1.5 s). The test harness then waited 60 s for a reply that correctly never came, streaming silence to Flux; the scenario runner should end on a timeout when no reply is expected.

## Consequences

Default stays `immediate` until the duck mode is measured with Flux interims. Echo on the Pi will make VAD fire on the bot's own voice; ducking would then attenuate the bot needlessly, so the echo work in Phase 2 comes before turning duck mode on there.

## Sources

* Installed source: `pipecat/transports/base_output.py` (`_filter_audio` runs on dequeue), `pipecat/turns/user_start/base_user_turn_start_strategy.py` (`enable_interruptions`), `pipecat/processors/frame_processor.py` (`broadcast_interruption`)
* docs/architecture.md, "Barge-in (user speaks while the agent talks)"
