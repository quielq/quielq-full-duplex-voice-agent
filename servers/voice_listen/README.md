# voice-listen (not built yet)

FastMCP server for ASR control. Tools: `start_listening`, `stop_listening`, `set_vocabulary`, `status`. Notifications: `speech_started`, `speech_ended`, `transcript_partial`, `transcript_final`. Audio arrives on a local side channel, not over MCP. Scheduled for step 6 (VoiceMCPBridge) and the voice-server work after Gate 1. See docs/architecture.md, "Voice servers".
