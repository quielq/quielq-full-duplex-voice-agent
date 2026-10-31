# ADR-012: Discard stale Aura-2 audio after an interruption

Date: Oct 31, 2026. Status: implemented. The socket replacement path is tested with fakes only.

## Context

A live mic run on Oct 31 (`events-20261031-141542-cfb8aa`) played audio from an interrupted reply at the start of the next turn. `first_audio_out` came about 0.76 s before `llm_first_token`.

Deepgram's `/v1/speak` websocket carries no context ID, so Pipecat 1.13.0 labels each chunk with the active context (`deepgram/tts.py`, `_receive_messages`). After an interruption the active context is empty and chunks are dropped. Once the next LLM response opens a context, late chunks take its ID, and `append_to_audio_context` recreates it and plays them. Pipecat ignores the `Cleared` reply, and a stale `Flushed` ends the new context in the same way.

Deepgram's docs say the server processes remaining audio and then returns `Cleared`. They do not state ordering or a time limit.

Two live runs of `bench.interruption_test --tts deepgram_aura2` measured this, each with one interruption after `tts_clear_sent`:

| Run | Result after Clear | Stale chunks seen | Last reply playback |
|---|---|---|---|
| `20261031-144435-1db368` | `Cleared` after 227 ms, last stale chunk at 211 ms | 13 | 1.78 s |
| `20261031-143254-fe0c18` | No `Cleared` within 1.177 s, when a 1.0 s timeout let audio through | 12 | 16.83 s |

In the second row, audio arrived 143 ms after `tts_request` rather than 287 ms and played for 16.83 s, so it was the old story. Clear was ignored or badly delayed in one run of two. That is too few runs to give a rate.

## Decision

`harness/deepgram_tts.py` adds `ClearAwareDeepgramTTSService`:

* After sending `Clear`, it discards audio and `Flushed` until `Cleared` arrives.
* `run_tts` holds the next reply until `Cleared`, for at most `CLEAR_TIMEOUT_S` (0.5 s) from the `Clear`.
* On timeout it closes the socket and lets `run_tts` reconnect. It never starts accepting audio on an uncleared socket, because the 1.0 s version did that and played the old story.
* It closes only the socket, not `_disconnect()`, which would also close the context of the reply about to speak.
* The event log records `tts_clear_sent` and `tts_cleared` (`clear_ms`, `stale_chunks`, `last_stale_ms`, `timed_out`).

The filter wraps `_get_websocket()` so that `_receive_messages` is not copied from Pipecat. This relies on upstream reading messages with `async for` over that call.

## Alternatives

* **`DeepgramHttpTTSService`:** each request has its own context, so the race cannot happen. It changes the transport and likely the latency, which breaks comparison with earlier runs. Not measured. This is the fallback if reconnects prove too frequent.
* **Reconnect on every interruption:** simple, but it adds connection time to every barge-in.
* **Accept audio after the timeout:** tried first, and it played the old reply.
* **Cartesia only:** planned for truncation tests and final runs, but Aura-2 stays the bulk development voice.

## Consequences

* Replies after an interruption can wait up to 0.5 s for `Cleared`. When the socket is replaced, reconnection adds more. That cost is not measured.
* The new reply is only held if it is ready before `Cleared`. In the good run it was not.
* `tests/test_deepgram_tts.py` asserts the bug on stock `DeepgramTTSService`. If that test fails after a Pipecat bump, check whether this workaround can go.
* `_drop_socket` has not run against live Deepgram.
* `CLEAR_TIMEOUT_S` rests on one measurement. Review it using `tts_cleared` events from later runs.

## Sources

* https://developers.deepgram.com/docs/tts-ws-clear
* https://developers.deepgram.com/reference/transform-text-to-speech-websocket
* Installed source: `pipecat/services/deepgram/tts.py`, `pipecat/services/tts_service.py`
