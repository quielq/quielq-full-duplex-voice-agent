# ADR-006: Context truncation after barge-in, measured

Date: Oct 21, 2026. Status: accepted (Cartesia arm pending purchase).

## Context

Step 4 asks whether the LLM context holds only the words actually spoken after a barge-in, comparing Cartesia (word timestamps) with Aura-2. The review expected Aura-2 to commit unspoken text, and recommended SENTENCE aggregation.

## Method

`bench/interruption_test.py`: a `say` voice asks for a story; 2.5 s after the bot starts speaking, a second `say` clip says "Stop right there." Both run through Silero VAD and Smart Turn in real time. STT and LLM are fakes so only the TTS varies. Output goes to `PacedNullOutputTransport`, a silent output paced like a sound card that records what played and when. Heard words are estimated from played seconds (exact 60 ms per character for the fakes; 850 characters per minute for real TTS). Since Nov 4 the bench uses word timestamps when the TTS has them, because the pace guess undercounted Cartesia (research.md, "Cartesia over-commit on interruption").

## Results (one run each, Oct 21, 2026)

| TTS | Word timestamps | Played | Heard words | Committed words (of 86) | Stop latency | Cost |
| --- | --- | --- | --- | --- | --- | --- |
| Fake, no timestamps | no | 2.8 s | 8 | 0 | 240 ms | $0 |
| Fake with timestamps (`fake_words`) | yes | 2.8 s | 8 | 9 | 267 ms | $0 |
| Deepgram Aura-2 (WebSocket) | no | 2.6 s | 6 | 86 (whole reply) | 291 ms | $0.015 |
| Cartesia Sonic | yes | pending | | | | needs Pro plan |

Stop latency is from the first loud sample of the barge-in to the last voiced output sample. It splits into VAD onset (215 to 235 ms after speech, with `start_secs` 0.08) and the output flush (3 to 8 ms). VAD dominates, so the 100 ms duck target depends on the DuckingProcessor reacting before a full VAD start (step 5).

## Findings

1. **Word timestamps work as designed.** Pipecat releases each word to the context when it starts playing, so the context matches what was heard to within one word. A Cartesia run should match the `fake_words` row; that is the remaining check.
2. **Aura-2 over-commits the whole reply**, as the review expected. Everything generated is in the context although only about 6 words played.
3. **A TTS without timestamps on the plain `TTSService` path under-commits:** a sentence is committed only after it finishes playing, so a barge-in mid-sentence commits nothing of it. This differs from the Aura-2 WebSocket path.
4. **A pause inside a short command splits it.** "Stop, wait." became two VAD utterances with `stop_secs` 0.2. Relevant for the duck-then-decide window and the keyword allowlist.
5. **Pipecat's Aura-2 WebSocket service emits no TTS usage metric.** The cost meter now counts TTS characters from the text sent to TTS for every provider (it errs high on interruption).

## Decision

* Truncation tests and final benchmark runs use a word-timestamped TTS (Cartesia), as planned.
* Until Cartesia is bought, `fake_words` is the free regression test for truncation (`tests/test_interruption.py`).
* Aura-2 stays the bulk-development TTS, with the known over-commit. Do not draw truncation or "Resume" conclusions from Aura-2 runs.

## Sources

* Installed source: `pipecat/services/tts_service.py` (word timestamps, `push_text_frames`), `pipecat/services/elevenlabs/tts.py` (cumulative time pattern), `pipecat/services/deepgram/tts.py`
* https://github.com/pipecat-ai/pipecat/issues/5605 (Flux TTS has no playback offset)
* https://github.com/pipecat-ai/pipecat/issues/5426 (token aggregation commits unspoken text)
