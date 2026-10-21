# voice-speak

Stub built (`stub_server.py`, step 6): simulates playback and streams `playback_started`, `played_up_to` (utterance id, word index, ms offset), `playback_stopped`, `playback_finished` while a `speak` call is open. `stop`, `duck(gain)`, `unduck` act concurrently. Not built yet: `append_text`, `pause`, `resume`, `interrupt(kind)`, `status`, a real TTS provider, the audio side channel. See ADR-008.
