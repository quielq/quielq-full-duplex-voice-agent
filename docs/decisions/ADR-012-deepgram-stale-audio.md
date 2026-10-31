# ADR-012: Discard stale Aura-2 audio after an interruption

Date: Oct 31, 2026. Status: implemented, tested with fakes only.

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

A live speaker run (`20261031-152235-130263`) then interrupted the agent three times. Deepgram sent 9 and 27 stale chunks, up to 1.77 s after `Clear`, and no `Cleared` arrived in any of the three. That makes four unanswered `Clear` messages of five.

## Decision

`harness/deepgram_tts.py` adds `FreshSocketDeepgramTTSService`. On interruption it stops reading the socket at once, closes it in the background, and opens a new one. The next reply waits only for that connection if it is not ready yet. No old audio can arrive, because nothing is listening to the old stream.

The event log records `tts_socket_reopened` (`reopen_ms`).

The first version discarded audio until `Cleared`, with a timeout that then replaced the socket. It was dropped: it depended on a reply Deepgram rarely sent, and waiting for the close handshake and a reconnect held replies back by 2.6 to 3.8 s in that live run.

## Alternatives

* **`DeepgramHttpTTSService`:** each request has its own context, so the race cannot happen. It changes the transport and likely the latency, which breaks comparison with earlier runs. Not measured.
* **Wait for `Cleared`:** tried, see above.
* **Accept audio after a timeout:** tried, and it played the old story for 16.83 s.
* **Cartesia only:** planned for truncation tests and final runs, but Aura-2 stays the bulk development voice.

## Consequences

* A reply starting less than one connection time after an interruption waits for it. Connections took about 0.9 to 1.2 s live, and the next reply usually comes at least that long after the interruption, but `reopen_ms` must confirm this.
* Each interruption opens a new connection. Check that Deepgram does not rate-limit this during long runs.
* `tests/test_deepgram_tts.py` asserts the bug on stock `DeepgramTTSService`. If that test fails after a Pipecat bump, check whether this workaround can go.
* This has not yet run against live Deepgram.

## Sources

* https://developers.deepgram.com/docs/tts-ws-clear
* https://developers.deepgram.com/reference/transform-text-to-speech-websocket
* Installed source: `pipecat/services/deepgram/tts.py`, `pipecat/services/tts_service.py`
