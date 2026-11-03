# ME3 Claude Code Prompt (Revision 2, Oct 21, 2026)

Paste everything below the line into a new Claude Code session on the MacBook Air. Attach `ME3_Architecture_Review.md` (and `ME3_Research.md` if you want it in the repo) first.

---

You are helping me (Quiel Quiwa, UP Diliman MEng AI, course AI222) build ME3: a generic, hands-free, full-duplex, English-only voice AI agent. The final target is a Raspberry Pi 5 that runs only VAD, Smart Turn, and audio I/O, with ASR, TTS, and the LLM in the cloud. We build Phase 1 on this MacBook Air (headphones) and Phase 2 on the Pi (speakerphone), so every choice must transfer to the Pi easily.

The attached `ME3_Architecture_Review.md` is the source of truth for design. Read it fully before doing anything. If this prompt and the review disagree, tell me and follow the review unless I say otherwise.

## Working rules

1. **Plan first, then wait for my approval.** Your first reply is a short plan (under 40 lines) covering the repo layout, the pinned versions you intend to use, and the steps of the first spike. Do not write code until I approve.
2. **Never make a paid call or create a paid account without asking me first.** The default for every provider is a fake (recorded or scripted) implementation. Real providers are switched on per config, one at a time, after I approve.
3. **Keys come from `.env`.** Commit only `.env.example`. Never print or log a key. Never commit audio recordings of people.
4. **Verify, do not assume.** Check versions, flags, and APIs against current docs or the installed package before using them. When something differs from the review, say so and cite the URL in an ADR.
5. **No em dashes or en dashes** in any document, comment, or commit message. Use commas, colons, or periods.
6. **Small commits** with clear messages, one concern each.
7. **Cost control.** Every provider call goes through `cost_ledger` with a hard daily cap per provider. A cap of zero means the call is refused.
8. **The repo is public** as a course requirement (changed Nov 3, 2026). Nothing private goes in it: keys, `.env`, recordings of people, `data/`, or `logs/`.
9. Report plainly. If a step fails, say what you tried and what happened. Do not retry the same failing command more than twice.

## Hard technical rules (from the research pass)

- **Never use TEN VAD.** Its licence bars competing with Agora's offerings. Use Silero VAD (MIT).
- **Use only Pipecat 1.x APIs.** Do not use `allow_interruptions`, `interruption_strategies`, `MinWordsInterruptionStrategy`, or `TransportParams(vad_analyzer=...)`. These were removed or deprecated and stale arguments are silently ignored. Use `user_turn_strategies` (for example `MinWordsUserTurnStartStrategy`, `VADUserTurnStartStrategy`) and `LocalSmartTurnAnalyzerV3` for stop.
- **Keep SENTENCE aggregation** for TTS text. Token aggregation has a known bug.
- **MCP carries control only.** Audio flows on a local side channel (Unix socket or shared memory). MCP tools are `listen_start`, `listen_stop`, `speak`, `stop_speaking`, `duck`, `unduck`, `interrupt`, plus notifications such as `speech_started` and `played_up_to` (word index and millisecond offset).
- **`MCPClient` has no notification hook.** Build a `VoiceMCPBridge` service using the raw MCP SDK `ClientSession` with a message handler. Use `MCPClient` and `register_tools(llm)` for ordinary tools.
- **Pin versions together.** Use `pipecat-ai` on the current 1.x (1.13.0 at last check), `fastmcp>=4.1,<5`, and `mcp>=2,<3`. Pipecat pull request 5624 widens MCP SDK 2.x support; confirm it is merged in the pinned Pipecat, otherwise pin `mcp<2`. Test both stdio and streamable HTTP.
- **Truncating context to what was heard needs word timestamps.** Cartesia, ElevenLabs WebSocket, and Rime provide them. Deepgram Aura-2 and Flux TTS do not. Use Aura-2 for bulk development and Cartesia for truncation tests and final benchmark runs.
- **Every MCP tool carries annotations** (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`). Annotations are hints only, so the harness enforces policy: destructive tools default to a mock and have a roughly 5 s undo window.
- **Latency is measured from audio timestamps**, not from framework logs. Pipecat metrics frames are for attribution only.
- **Python versions:** 3.12 on the Mac, 3.13 on Pi OS Trixie. Manage with `uv`.
- **Duck-then-decide** is custom: a processor before transport output scales audio by about 0.3 when speech starts, then within about 0.5 s either lets it resume or cancels, using a keyword allowlist (stop, wait, no, cancel, pause). Target a duck within 100 ms using `start_secs` of about 0.064 to 0.1 (Silero defaults declare speech after about 192 ms).
- **Echo:** Pipecat has no AEC. Phase 1 uses headphones. Phase 2 tries a hardware-AEC speakerphone first, then PipeWire echo-cancel (WebRTC backend), then an RMS gate.

## Repo to create

Create a private GitHub repo `me3-voice-agent` with `gh` (if `gh` is not authenticated, stop and ask me). Layout:

```
harness/                    Pipecat pipeline, VoiceMCPBridge, DuckingProcessor, cost-aware gating
servers/voice_listen/       FastMCP server for ASR control
servers/voice_speak/        FastMCP server for TTS control
servers/tasks/{music,lights,clock,thermostat,notes,comms,knowledge,delegate}/
assets/fallback_wavs/       Pre-synthesised phrases for offline use
bench/{fdb_adapter.py, latency_probe.py, task_suite/, cost_ledger.py}
configs/{mac.yaml, pi.yaml, providers.yaml}
docs/{research.md, architecture.md, decisions/ADR-*.md}
scripts/{pi_setup.sh, pipewire_aec.conf}
tests/
CLAUDE.md
.env.example
```

Copy the attached architecture review to `docs/architecture.md` (and the research doc to `docs/research.md` if attached). Write a `CLAUDE.md` that summarises the working rules and hard technical rules above so every future session follows them.

## First spike (after I approve your plan)

Do these in order. Stop at the end of step 3 for Gate 1 and report.

1. **Pin and install.** `pipecat-ai[silero,local,deepgram,cartesia,groq,anthropic,mcp]` on current 1.x, `fastmcp>=4.1,<5`, `mcp>=2,<3` (confirm PR 5624, otherwise `mcp<2`). Record the exact versions in `docs/decisions/ADR-001-versions.md`.
2. **Minimal Mac bot** with `LocalAudioTransport` on headphones: Deepgram Flux for ASR, Groq gpt-oss-20b for the LLM, Cartesia for TTS. Default to fakes until I approve real keys. Log per-turn timestamps.
3. **FastMCP clock server** called through `MCPClient` over stdio and again over streamable HTTP, driven by a spoken request ("set a timer for one minute"). **This is Gate 1.** If it fails, register tools by hand with the MCP Python client and pass them to Pipecat as function handlers, then tell me.
4. **Interruption test.** Confirm the LLM context holds only the words that were actually spoken after a barge-in, comparing Cartesia (word timestamps) with Aura-2.
5. **DuckingProcessor prototype** with the keyword allowlist, and measure speech onset to volume drop.
6. **VoiceMCPBridge** receiving `speech_started` and `played_up_to` notifications from a stub voice server.
7. **Pi port** (Phase 2, only after I say go): speakerphone, PipeWire echo-cancel, CPU and temperature logging, Smart Turn timing on a single core, and the cost of the extra local hop through our MCP voice servers.

Steps 4 to 7 follow Gate 1 and my approval. I am away Oct 24 to 28, so aim to close Gate 1 by Oct 23.

## Do not build yet

- Barge-in logic beyond the stubs in step 5
- No-wake-word addressing filters
- The full benchmark harness (only `cost_ledger.py` and a skeleton `latency_probe.py`)
- The other task servers beyond `clock`
- The model comparison
- Anything on the Pi

## Budget and spending

The proposed development budget is about $20 cash: Deepgram on its free credit, Cartesia Pro $5, Groq about $3, Anthropic $5, OpenRouter $5, reserve $2. Nothing is bought until I approve it. Ask before each purchase, and tell me the exact amount and provider.

## ADRs

For every decision that departs from the review or settles an open question, add `docs/decisions/ADR-NNN-title.md` with: context, decision, alternatives, consequences, and the source URL.

## Your first reply

Give the plan described in rule 1, list any places where this prompt or the review looks wrong or out of date, and ask only the questions you cannot answer yourself.
