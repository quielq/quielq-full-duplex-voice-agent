# ADR-032: Voice servers that hold the Flux and Cartesia streams

Date: Nov 4, 2026. Status: option B chosen by Quiel on Nov 4, 2026. Built and tested on fakes. Not run live yet.

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

- **Measured on fakes, Nov 4:** a spoken turn with `stt=voice_listen,tts=voice_speak` set a timer and replied over stdio and HTTP. First audio came 70 ms after the end of the turn, and voice events took 2 to 7 ms over MCP. The extra hop for audio frames is not measured yet, on the Mac or the Pi.
- **The key stays in the server process,** and Pipecat's services never receive it. The harness process still loads `.env`, so this keeps the key out of the pipeline, not out of the machine.
- Each server is one more process on the Pi, about the size of a task server.
- A stream that drops is reopened by Pipecat's reconnect logic. That logic now calls `listen` or `speak` again.
- The stubs and `bench/voice_bridge_probe.py` stay as they are, for the hop measurement.

## Sources

- Pipecat 1.13.0, installed: `pipecat/services/deepgram/flux/stt_base.py` (transport-agnostic Flux base), `pipecat/services/websocket_service.py` (`_websocket_connect`), `pipecat/services/cartesia/tts.py` (message types `chunk`, `timestamps`, `done`, `error`, `flush_done`).
- Deepgram Flux configuration: https://developers.deepgram.com/docs/flux/configuration.md
- websockets 17.3, installed: `websockets.connect(additional_headers=...)`.
- ADR-008 (voice events on a long-running call), ADR-013 (echo cancelling in the harness), ADR-015 (Cartesia as the live TTS).
