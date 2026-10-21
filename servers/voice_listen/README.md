# voice-listen

Stub built (`stub_server.py`, step 6): replays a scripted utterance and streams `speech_started`, `transcript_partial`, `transcript_final`, `speech_ended` as voice events while a `listen` call is open. `stop_listening` ends it. No ASR provider yet; audio will move on a local side channel, not over MCP. See ADR-008 for why events ride a long-running call.
