# ADR-032: Voice servers that hold the Flux and Cartesia streams

Date: Nov 4, 2026. Status: option B chosen by Quiel on Nov 4, 2026. Built and tested on fakes. Run live on the Mac on Nov 4: the interruption bench through `voice-speak`, and an 18 turn mic run through both servers (Results). Not run on the Pi yet.

## Context

The spec asks for MCP servers that attach to state of the art ASR and TTS, built and run by us on the Pi (items 2 and 5). Until now `voice-listen` and `voice-speak` were stubs for the bridge probe (ADR-008), and the live path opened Flux and Cartesia straight from Pipecat.

Three shapes were weighed on Nov 3:

- **A, full servers:** the servers own the audio device, playback, and ducking, as `docs/architecture.md` drew them.
- **B, thin servers:** the servers own the provider connection and its key, and relay it to the harness on a local socket. Playback, ducking, and echo cancelling stay in the harness.
- **C, stubs only:** keep audio in Pipecat and show the bridge with the stub probe.

Quiel chose B on Nov 4.

## Decision

- `servers/voice_listen/server.py` holds the Deepgram Flux stream. Its tools are `listen(query)`, `stop_listening(session)`, and `status()`.
- `servers/voice_speak/server.py` holds the Cartesia stream. Its tools are `speak(query, version)`, `stop(session)`, and `status()`.
- `servers/voice_relay.py` is the code both servers share. A `listen` or `speak` call checks the daily cap and the key, then opens a Unix socket in a private folder (0700 folder, 0600 socket) with a one-time token. It announces both with a `stream_ready` voice event and stays open while the stream lives. The harness connects and sends the token. The server then opens the provider WebSocket and relays frames both ways.
- **Socket frames:** one kind byte (`B` binary, `T` text), a 4-byte big-endian length, then the payload. The provider's own messages pass through unchanged.
- **The upstream address is fixed in each server.** The harness supplies only the query string, so it cannot point the server, or its key, anywhere else. Tests may name a loopback upstream with `ME3_VOICE_LISTEN_UPSTREAM` or `ME3_VOICE_SPEAK_UPSTREAM`. Any other host is refused.
- **Voice events (ADR-008)** go out on the `voice` logger while the call is open:
  - listen: `speech_started`, `transcript_eager`, `speech_resumed`, `transcript_final`.
  - speak: `audio_started` (the first chunk of a reply), `audio_done`, `tts_error`.
  - both: `upstream_connected` with the connect time, `stream_refused`, `stream_closed` with a reason.
- The token and the socket path are never written to the event log.
- **The harness side** (`harness/voice_relay.py`) keeps Pipecat's own `DeepgramFluxSTTService` and `CartesiaTTSService`. A mixin replaces their `_websocket_connect` with an MCP call and returns a stand-in WebSocket over the socket. Turn events, the Flux watchdog, reconnects, word timestamps, and cancelling on interruption all stay Pipecat's.
- New stages are `stt=voice_listen` and `tts=voice_speak`. Defaults stay fake, and `deepgram_flux` and `cartesia` stay available for comparison.
- **The cost ledger:** both the harness and the server check the cap before a stream opens. Spend is still metered once, in the harness `CostObserver`, from the same frames, into the same ledger file.

## Departures from the architecture

- **Ducking stays in the harness, not in `voice-speak`.** The harness plays the audio, so the gain is applied where the buffer is. Moving playback into the server would also move the echo canceller's reference out of the harness. `duck`, `unduck`, and `interrupt` are therefore not server tools. Interrupting a reply is Cartesia's own cancel message on the stream, sent by Pipecat.
- The server tools are fewer than the table in `docs/architecture.md` (`set_vocabulary`, `append_text`, `pause`, `resume`). Vocabulary and every other Flux setting go in the `listen` query, and Configure messages pass through the stream.
- The side channel is a Unix socket, which settles that open decision. Shared memory was not tried, because the audio is small: 32 KB a second in, 48 KB a second out.

## Alternatives

- **A, full servers:** closest to the original drawing. It moves playback, ducking, and the echo canceller across a process boundary, five days before the demo, on a Pi with no hardware AEC.
- **C, stubs only:** no new risk, but it does not meet spec items 2 and 5.
- **Audio inside MCP calls:** rejected in the review. A request and response tool cannot be cancelled in tens of milliseconds.
- **One server for both:** simpler, but the spec names ASR and TTS servers, and a failure in one would take down both.

## Consequences

- **Measured on fakes, Nov 4:** a spoken turn with `stt=voice_listen,tts=voice_speak` set a timer and replied over stdio and HTTP. First audio came 70 ms after the end of the turn, and voice events took 2 to 7 ms over MCP. The extra hop for audio frames was measured on the Mac the same day and on the Pi on Nov 5, below.
- **Audio hop on the Mac, Nov 4** (`bench/voice_hop_probe`, 250 frames of 20 ms, loopback fakes, MacBook Air arm64). The relay against a direct WebSocket to the same fake, p50 added (relay p95 in brackets):
  - 16 kHz frames up, one way: no measurable cost (relay 0.21 ms p50, 0.52 ms p95 on stdio; 0.20 ms and 0.32 ms on HTTP). The direct path was slower here because the fake and the client share one event loop, so up to about 0.3 ms is noise.
  - 24 kHz chunks down, one way: +0.33 ms on stdio, +0.39 ms on HTTP (relay p95 1.6 ms and 1.3 ms).
  - Request to first chunk: +0.6 ms on stdio, +2.1 ms on HTTP (relay p95 17 ms and 8 ms). The relay waits for each `audio_started` and `audio_done` event to go out over MCP before it forwards the next message, and HTTP makes that wait longer.
  - Stream open, call to `upstream_connected`: +21 ms on stdio, +5.5 ms on HTTP at p50 (a 20-frame run gave +6 ms on stdio). The first open of a session also starts the server: 0.75 to 0.87 s.
  - So the hop adds well under 1 ms to each audio frame, against a 20 ms frame and a 70 ms turn-to-audio budget. Opens cost tens of ms, and the warm reconnect path should be timed on the Pi.
- **After sending events from a queue (a3bec1e), Nov 4,** same probe and size, one run (`logs/voice-hop-20261104-131352.json`). Request to first chunk: relay p95 3.3 ms on stdio (was 17 ms) and 6.2 ms on HTTP (was 8 ms), +0.7 ms and +1.0 ms at p50. Chunks down: +0.2 ms at p50 on both. Stream open: +4 ms on stdio and +5 ms on HTTP at p50. Cold opens stayed at 0.75 to 0.97 s. One run on a shared laptop, so single maxima up to about 18 ms are noise.
- **The key stays in the server process,** and Pipecat's services never receive it. The harness process still loads `.env`, so this keeps the key out of the pipeline, not out of the machine.
- Each server is one more process on the Pi, about the size of a task server.
- **Recovery, tested on fakes, Nov 4** (`tests/test_voice_reconnect.py`, both servers, stdio and HTTP). A stream that drops is reopened by Pipecat's own reconnect logic, which calls `listen` or `speak` again through `RelayedConnection`. Flux reconnects from its receive loop; Cartesia reconnects at once when its stream ends, as it does after its 5 minute idle timeout.
  - The provider closes the stream: the server logs `stream_closed` with the provider's code and reason, such as `provider closed the stream (1011 idle timeout)`, then the next open logs a new `stream_ready`. In a whole bot run where both fakes dropped after the first turn, the new stream was ready 230 ms (listen) and 37 ms (speak) after `stream_closed`, and the second turn was heard and spoken.
  - The provider refuses the connect: the server now reaches the provider before it offers the socket, so a refusal fails the `listen` or `speak` call with `stream_refused` and the provider's status (a 503 in the test), the way a refused WebSocket fails Pipecat's own connect. Pipecat retries. Its first wait is set to 1 s for the relayed services, not its default 4 s, because the stream is local. Pipecat also stops after three connections in a row that last under 5 s each, which still applies.
  - The server process dies: every stream on it ends, and the next open finds the MCP session dead (the call fails at once with `Connection closed` on stdio, `SSE stream ended` on HTTP). `RelayClient` logs `voice_server_restart`, starts a new server process, and retries once. No `stream_closed` comes from a dead server. The reopen took under 2 s including the new process.
  - A stop during a connect: the stream is registered before the provider answers, so `stop` or `stop_listening` ends it with `stream_closed` reason `stopped` and no socket is offered. The bot stopping during a reconnect (`RelayClient.close`) ends the pending call within about 2 s, and later opens refuse.
  - An interruption while the stream is down: Pipecat's cancel message fails with `ConnectionError`, and Pipecat catches it and still clears the reply (`tts_service.py`, around line 1337). The cancelled reply died with the old stream anyway.
  - Nothing leaks: after each case the server reports no open stream and no socket in its private folder (`status` now counts both), the harness has no open MCP call, and no server process is left. A cancelled `listen` or `speak` call (the harness went away) closes its provider stream and socket too. The private folder is removed when the server exits normally.
  - An idle MCP call over HTTP does not time out: the MCP SDK's SSE read timeout is 300 s, and the server's SSE stream sends a ping every 15 s (sse-starlette's default). Not tested for the full 5 minutes.
  - Flux's drop is also reported as a non-fatal `pipeline_error` ("connection closed by server") by Pipecat, the same as with a direct Flux connection.
  - `upstream_connected` now comes before `stream_ready`, so `bench/voice_hop_probe`'s open time no longer includes the socket handshake, which is under 1 ms.
- **Interruption through `voice-speak`, live, Nov 4** (`bench.interruption_test --tts voice_speak`, run `20261104-202147-ab3d44`, $0.0246). Cartesia truncates the same through the relay as direct: 9 words heard, 10 committed, over-commit 1, stop 277 ms (direct: 245 ms, mostly VAD onset; flush 7 ms against 9 ms). This was the first live run of a reply cancelled through the relay.
- **First live mic run through both servers, Nov 4** (`20261104-202417-64248a`, MacBook Air speakers with `--echo-cancel`, Haiku 5.5, 18 turns, $0.21). Both streams opened (Cartesia 494 ms, Flux 786 ms), stayed up for the whole 5 minute run, and every turn was heard and spoken. Transcript events took 16 to 38 ms over MCP. Interrupting the story through `voice-speak` worked by voice. One turn was the bot hearing its own reply through the speakers ("I can't delete"), so the echo canceller let a few words through at speaker volume.
- **Hop cost on the Pi, Nov 5** (`bench/voice_hop_probe`, 250 frames, 10 opens, loopback fakes, Raspberry Pi 5, `logs/voice-hop-20261105-220737.json` on the Pi). Added at p50, relay against direct: frames up +0.11 ms (stdio) and +0.10 ms (HTTP), chunks down no measurable cost, request to first chunk +0.95 ms and +0.62 ms (relay p95 1.4 ms and 1.9 ms), stream open +16 ms and +18 ms. Cold opens, server start included, 1.3 to 1.7 s, against 0.75 to 0.97 s on the Mac. So the hop still adds well under 1 ms a frame on the Pi; only the first open of a session is slower.
- **MCP events on the Pi, Nov 5** (`bench/voice_bridge_probe`, stubs): event hop p50 2.0 ms (stdio) and 2.2 ms (HTTP), p95 3.3 ms and 4.3 ms, duck round trip 4.0 and 6.6 ms, stop to `playback_stopped` 4.6 and 7.1 ms. In line with the Mac numbers in ADR-008.
- **First live run on the Pi through both servers, Nov 5** (`20261105-203514-1a2f7a`, USB mic and speaker with WebRTC AEC, Haiku 5.5, 30 turns, $0.40). Upstream connects about 0.7 s each, relay event hops 3 to 12 ms, both streams up for the whole 11 minutes.
- The stubs and `bench/voice_bridge_probe.py` stay as they are, for the hop measurement.

## Sources

- Pipecat 1.13.0, installed: `pipecat/services/deepgram/flux/stt_base.py` (transport-agnostic Flux base), `pipecat/services/websocket_service.py` (`_websocket_connect`), `pipecat/services/cartesia/tts.py` (message types `chunk`, `timestamps`, `done`, `error`, `flush_done`).
- Deepgram Flux configuration: https://developers.deepgram.com/docs/flux/configuration.md
- websockets 17.3, installed: `websockets.connect(additional_headers=...)`.
- ADR-008 (voice events on a long-running call), ADR-013 (echo cancelling in the harness), ADR-015 (Cartesia as the live TTS).
