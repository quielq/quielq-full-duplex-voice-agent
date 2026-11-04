# voice-speak

`server.py` (ADR-032) holds the Cartesia stream and its key. `speak(query, version)` checks the cap, opens a private Unix socket, and relays Cartesia both ways while the call stays open. It reports `audio_started`, `audio_done`, and `tts_error` per reply. `stop` ends a stream. Playback, ducking, and echo cancelling stay in the harness. The harness reaches it with `tts=voice_speak`.

`stub_server.py` (step 6) simulates playback with `played_up_to`, `duck`, and `unduck` for `bench/voice_bridge_probe.py`. See ADR-008.
