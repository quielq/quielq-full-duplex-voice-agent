# voice-listen

`server.py` (ADR-032) holds the Deepgram Flux stream and its key. `listen(query)` checks the cap, opens a private Unix socket, and relays Flux both ways while the call stays open. It reports `speech_started`, `transcript_eager`, `speech_resumed`, and `transcript_final` as voice events. `stop_listening` ends a stream. The harness reaches it with `stt=voice_listen`.

`stub_server.py` (step 6) replays a scripted utterance for `bench/voice_bridge_probe.py`. See ADR-008 for why events ride a long-running call.
