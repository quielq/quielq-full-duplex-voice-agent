# ME3 voice agent: rules for every session

ME3 is a hands-free, full-duplex, English-only voice agent for AI222 (UP Diliman MEng AI). Phase 1 runs on a MacBook Air with headphones; Phase 2 runs on a Raspberry Pi 5 with a separate USB mic and USB speaker and a 7-inch touch display. Only VAD, Smart Turn, and audio I/O run locally; ASR, TTS, and the LLM are cloud APIs.

`docs/architecture.md` is the source of truth for design. If an instruction and the review disagree, say so and follow the review unless Quiel says otherwise. Decisions that depart from it go in `docs/decisions/ADR-NNN-title.md` (context, decision, alternatives, consequences, source URL).

## Working rules

1. Plan first and wait for approval before writing code for a new step.
2. Never make a paid call or create a paid account without asking first. Every provider defaults to a fake; real providers are switched on one at a time in `configs/providers.yaml` after approval.
3. Keys come from `.env` only. Commit `.env.example`, never `.env`. Never print or log a key. Never commit audio recordings of people.
4. Verify, do not assume: check versions, flags, and APIs against current docs or the installed package. Record differences from the review in an ADR with the URL.
5. No em dashes or en dashes in documents, comments, or commit messages. Use commas, colons, or periods.
6. Small commits, one concern each, with clear messages.
7. Every provider call goes through `bench/cost_ledger.py` with a hard daily cap per provider. A cap of zero refuses the call.
8. The repo is public as a course requirement (Nov 3, 2026). Nothing private goes in it: keys, `.env`, recordings of people, `data/`, or `logs/`.
9. Report plainly. If a step fails, say what was tried and what happened. Do not retry the same failing command more than twice.

## Hard technical rules

- Never use TEN VAD (its licence bars competing with Agora). Silero VAD only.
- Pipecat 1.x APIs only. Never `allow_interruptions`, `interruption_strategies`, `MinWordsInterruptionStrategy`, or `TransportParams(vad_analyzer=...)`; stale arguments are silently ignored. Use `user_turn_strategies` and `LocalSmartTurnAnalyzerV3` for stop. See ADR-003 for the 1.13 replacements (`PipelineWorker`, `WorkerRunner`, `client.tools()` instead of `register_tools`, `settings=` objects).
- Keep `TextAggregationMode.SENTENCE` for TTS (token aggregation commits unspoken text on interruption).
- MCP carries control only. Audio flows on a local side channel. Voice tools: `listen`, `stop_listening`, `speak`, `stop`, `duck`, `unduck`, `interrupt`. Events (`speech_started`, `played_up_to` with word index and ms offset, ...) are MCP logging notifications on the `voice` logger, sent while `speak` or `listen` is open, because the sessionless MCP protocol drops unrelated notifications (ADR-008).
- `MCPClient` has no notification hook. `harness/voice_bridge.py` (`VoiceMCPBridge`) uses the raw MCP SDK `ClientSession` with a logging callback. Use `MCPClient` for ordinary tools.
- Pins move together: `pipecat-ai==1.13.0`, `fastmcp>=4.1,<5`, `mcp>=2,<3` (PR 5624 is merged). Test stdio and streamable HTTP. Do not add the `local-smart-turn` extra (ADR-001).
- Truncating context to what was heard needs TTS word timestamps: Cartesia, ElevenLabs WebSocket, Rime. Not Deepgram Aura-2 or Flux TTS. Cartesia is the live TTS (ADR-015, chosen by Quiel Nov 1, 2026). Aura-2 stays available but cannot truncate.
- Every MCP tool carries `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`. They are hints: the harness enforces policy. Destructive tools default to a mock with a roughly 5 s undo window. `harness/tool_policy.py` enforces this (ADR-011): a tool result of `waiting_for_undo` or `needs_confirmation` means it has not run, and only a transcript can release it. Anything open world and destructive needs a `readback` in `configs/tool_policy.yaml`, so the harness asks from the real arguments and the model does not (ADR-024).
- Tool names must be unique across all servers (Pipecat flattens them), so prefer `list_timers` over `list`.
- Headline latency comes from audio timestamps. Pipecat metrics frames are attribution only.
- Python 3.13 on both machines, managed by uv (ADR-002).
- Duck-then-decide is custom: scale output by about 0.3 on speech onset, decide within about 0.5 s using the allowlist (stop, wait, no, cancel, pause). Duck within 100 ms with Silero `start_secs` of about 0.064 to 0.1.
- Pipecat has no AEC. Phase 1 uses headphones. Phase 2 uses a separate USB mic and speaker with no hardware AEC, so `pi.yaml` turns on WebRTC AEC in the harness (ADR-013, ADR-029); PipeWire echo-cancel, then an RMS gate, are the fallbacks. `--echo-cancel` adds it on the Mac for speaker tests.
- `write_file` and `edit_file` have no undo window, because a multi-step job needs each file at once. The server keeps replaced versions in `data/workspace/.versions` (ADR-028). Do not extend this to any tool that leaves the workspace.
- The robot face (`--ui`, ADR-027) reads the event log and stays on 127.0.0.1. It displays devices and only controls the mic mute, so nothing on screen can bypass the tool policy. With `--spotify-player` the page also plays Spotify (ADR-031, off by default), but it only plays: the `music` tools still choose and pause.

## Layout and commands

Running the full agent on the Mac: `docs/quickstart-mac.md`. The Pi port order: `docs/quickstart-pi.md`.

```
harness/        bot.py (entry point), fakes, providers, MCP clients, tool policy, addressing, recovery, waiting phrase, event log, cost observer, audio I/O, ui.py and ui_page.html (robot face), timer_watch.py, delegate_watch.py
servers/        voice_listen/, voice_speak/ (Flux and Cartesia relays, ADR-032, plus the stubs), voice_relay.py, tasks/clock, notes, thermostat, comms, lights, music (built, mocks except clock and notes; music has Spotify, ADR-022), knowledge, docs, workspace (built, ADR-021), delegate (built on fakes, ADR-035)
bench/          doctor.py, run_report.py, cost_ledger.py, latency_probe.py (skeleton), interruption_test.py, voice_bridge_probe.py, task_runner.py, task_suite/cases/, fdb_adapter.py (stub)
configs/        mac.yaml, pi.yaml (profiles), providers.yaml (stage selection, caps, prices), tool_policy.yaml
docs/           architecture.md, tools.md, research.md, prompt.md, decisions/
```

```
uv sync                                                   # install
uv run pytest -q                                          # tests, all fakes, no spend
uv run python -m harness.bot --input text --mcp stdio     # scripted turn, no audio
uv run python -m harness.bot --input wav --wav x.wav      # wav through VAD and Smart Turn
uv run python -m harness.bot                              # live mic (headphones)
uv run python -m harness.audio_io                         # list audio devices by name, with rate support
uv run python -m harness.ui --demo                        # robot face at http://127.0.0.1:8770, no bot, no spend
uv run python -m bench.doctor [--quick] [--offline]       # health check, no spend; run this first when anything is wrong
uv run python -m bench.run_report [--live] [<run id>]     # timeline and anomalies for one run
uv run python -m bench.run_report --list 20               # one line per recent run
uv run python -m bench.cost_ledger                        # today's spend per provider
uv run python -m bench.latency_probe logs/events-<id>.jsonl
uv run python -m bench.interruption_test --tts fake_words [--barge-in duck]   # step 4 and 5
uv run python -m bench.voice_bridge_probe                 # step 6, MCP hop cost
uv run python -m bench.task_runner --repeat 5             # task suite on fakes, no spend
uv run python -m bench.eot_sweep --grid eot_threshold=0.7  # end-of-turn sweep on fakes, no spend (ADR-018)
ME3_LIVE=1 uv run pytest -q tests/test_live.py            # live Gate 1, spends about $0.0014
```

Live runs use this command (Haiku 5.5, ADR-025; Groq's rate limits stop long runs). Defaults stay fake.

```
uv run python -m harness.bot --stages stt=deepgram_flux,llm=anthropic,tts=cartesia,music=spotify --echo-cancel --ui
```

Add `ME3_WORKSPACE_LIVE=1 ME3_TOOL_STAGES=search=tavily` in front for multi-stage jobs such as building a website (`docs/tools.md`).

 The bot starts all ten task servers; `--servers clock` narrows that. `bench.task_runner --stages llm=anthropic --all-servers` needs `ME3_LIVE=1`.

Spotify (demo only, ADR-022): register the redirect URI, run `uv run python -m bench.spotify_login` once, open the Spotify app, then add `music=spotify` to `--stages`. Audio plays in the Spotify app, not through the harness.

Live mic runs refuse the built-in speakers unless `--echo-cancel` is set (WebRTC AEC, ADR-013). `uv run python -m bench.echo_probe [--talker]` measures it with no cloud calls and reads aloud through the speakers.

General tools (ADR-021, ADR-024): weather and fetch are real by default, and `search` is a fake until `ME3_TOOL_STAGES=search=tavily`. File tools are real but confined to `data/workspace`, with `data/documents` read-only. `send_file` is always a mock.

`bash` and `serve` are mocks unless `ME3_WORKSPACE_LIVE=1`. Live, they pass `servers/command_policy.py` (ADR-034: reads run at once, changes and runs ask in plain words, `rm` keeps a copy) and run in `servers/sandbox.py` (`sandbox-exec` on the Mac, `bwrap` on the Pi). With no sandbox they refuse. Never weaken this.

`open_in_browser` is the only tool that leaves the sandbox: one HTML file from the workspace, after a spoken yes, and a mock unless `ME3_WORKSPACE_LIVE=1` (ADR-026). Do not widen it to other file types or to command text without a new ADR.

Messages and calls are mocks unless `ME3_TOOL_STAGES=comms=bridge`, which needs `scripts/mac_phone_bridge.py` running on the Mac, the bridge settings in `.env`, and `data/contacts.yaml`. Calls need a click on the Mac.

How tools work, how to add one, and which guardrails it needs: `docs/tools.md`. Read it before adding or changing a tool.

Addressing (`stay_silent`, ACTIVE/IDLE, level gate, mute) is built but off by default and has only run on fakes (ADR-016). Mute: `kill -USR1 <pid>` in mic mode.

Reply templates skip the second LLM call on simple tools, off by default (`--reply-templates`, ADR-020). `flux_forced` is built and off (ADR-019).

Not built yet (wait for approval): barge-in beyond the step 5 prototype (duck mode is off by default), the full benchmark, the model comparison. `delegate` is built on fakes only (ADR-035): a live worker needs `ME3_TOOL_STAGES=delegate=anthropic` and approval.

Voice servers (ADR-032): `stt=voice_listen,tts=voice_speak` routes Flux and Cartesia through our MCP servers, which hold the keys. The provider messages ride a Unix socket per stream, and playback, ducking, and AEC stay in the harness. Tested on fakes only.

Pi scripts are built and have not run on a Pi (ADR-030): the service stays on fakes until `~/.config/me3/bot.env` names live stages.

The waiting phrase is on (ADR-033): five clips rotate, after 1500 ms with no reply audio or when a slow tool starts, and the prompt tells the model not to say one itself. Until `ME3_LIVE=1 uv run python -m bench.make_phrases` makes the clips (paid, about $0.003), it is a short tone.
