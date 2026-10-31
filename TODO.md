# ME3 to-do list

Living list of open work. Tick items as they close, add new ones where they belong, and note the ADR or commit that settled each one. Last updated: Oct 31, 2026.

Status as of Oct 31: Gate 1 closed live (Flux, Groq, Aura-2, stdio and HTTP). Steps 5 and 6 done. Step 4 done except the Cartesia arm. No commits since Oct 21, so the build plan was redrawn (see "Replan, Oct 31" in `docs/architecture.md`).

## Waiting on Quiel

- [x] Approve ADR-009 and the Oct 31 replan (Oct 31)
- [ ] Decide whether real voice servers and the audio side channel stay in scope before the deadline (check the assignment spec first)
- [ ] Buy Cartesia Pro ($5), approve a cartesia cap (suggested $1.00/day), and pick a default English voice
- [ ] Try the live mic with headphones: `uv run python -m harness.bot --stages stt=deepgram_flux,llm=groq,tts=deepgram_aura2`
- [x] Confirm the deadline: Nov 7 holds (Oct 31)
- [ ] Check Thursday's command list in Google Classroom
- [ ] Decide the patent and privacy question before Nov 7 (repo stays private until then)
- [ ] Later purchases: Anthropic ($5) and OpenRouter ($5) for the model comparison; Spotify developer app (free) for `music`

## Finish the first spike

- [ ] Step 4, Cartesia arm: run `bench/interruption_test.py --tts cartesia` and confirm it truncates like `fake_words` (ADR-006)
- [ ] Step 7, Pi port (needs a go):
  - [ ] `scripts/pi_setup.sh` and a smoke test (portaudio19-dev, onnxruntime aarch64 wheels)
  - [ ] Speakerphone plus PipeWire echo-cancel (`scripts/pipewire_aec.conf`)
  - [ ] CPU and temperature logging
  - [ ] Smart Turn timing on one Pi core
  - [ ] MCP voice-hop cost on the Pi (`bench/voice_bridge_probe.py`)

## ADR-009 additions (approved)

Tool policy (Nov 1):

- [ ] Spike wrapping `MCPClient` handlers (no documented hook in Pipecat 1.13)
- [ ] `harness/tool_policy.py` and `configs/tool_policy.yaml`: defaults from MCP hints, `permission`, `undo_s`, `timeout_s`, disabled tools removed from the schema
- [ ] Spoken confirmation and 5 s undo, with "cancel" working in both barge-in modes
- [ ] Startup check for duplicate tool names; log every policy decision
- [ ] Protect only the commit step, never block the mic

Test layers (Nov 1 onward):

- [ ] `bench/task_runner.py` and `bench/task_suite/cases/`: next reply, tool call, conversation, audio timing
- [ ] `--repeat`, pass rates, fixed failure buckets, cost per successful task
- [ ] Clock cases first, then one set per new server
- [ ] Separate ledger price for `gpt-oss-120b`

Waiting phrase (Nov 2):

- [ ] Phrase cache, shared with the network-loss phrases and the spending-cap notice
- [ ] Triggers: tool call start, or no reply audio after `filler.after_ms`; off by default in `mac.yaml` and `pi.yaml`
- [ ] Once per turn, interruptible, kept out of the LLM context, never for `stay_silent` or in IDLE
- [ ] `filler_audio_out` event and a `latency_probe` stage; log how often it plays

## Latency (about 2.8 s now, target p50 under 1.0 s)

- [ ] Tune Flux end of turn: its `eot_threshold` setting and eager end of turn (`EagerUserTurnStrategies`) (ADR-005)
- [ ] Add Deepgram Nova-3 so Smart Turn can be A/B tested against Flux fairly
- [ ] Cut the gap after a tool call (two LLM round trips per tool turn); try streaming the confirmation
- [ ] Measure from real audio, not framework events: full `latency_probe`, p50/p90/p95 over 30+ runs

## Barge-in

- [ ] Replace the 100 ms history-ordering workaround: let the aggregator own the turn start (`ExternalUserTurnStartStrategy`) (ADR-007)
- [ ] Get duck-mode stop under 300 ms (about 600 ms with Flux now): local stop-word spotter, or act on Flux partial transcripts sooner
- [ ] Choose the default between `immediate` and `duck`, after the Flux and Pi echo measurements
- [ ] Test-runner fix: end on a timeout when no reply is expected (it waits 60 s and streams silence to Flux)
- [ ] Resume policy after an interruption: resume, summarize the unplayed part, or drop it (review improvement 4)

## Voice servers (stubs today)

- [ ] Real `voice-listen` and `voice-speak` backed by Flux and Cartesia/Aura-2
- [ ] Audio side channel: choose Unix socket or shared memory, then build it
- [ ] Missing tools: `append_text`, `pause`, `resume`, `interrupt(kind)`, `status`
- [ ] Wire `VoiceEventFrame`s into pipeline actions (ADR-008)
- [ ] Reconnect logic for MCP sessions that expire after 30 minutes idle
- [ ] Due timers announce themselves through `voice-speak`

## Task servers (only `clock` exists)

- [ ] `music` (Spotify, demo only)
- [ ] `lights` (check whether the Xiaomi bulb can be controlled locally)
- [ ] `notes` (SQLite)
- [ ] `thermostat` mock
- [ ] `comms` mock
- [ ] `knowledge` (search provider not chosen)
- [ ] `delegate` (onit in a per-task sandbox, with a token cap)

## No-wake-word addressing

- [ ] Loudness gate with 300 to 500 ms of saved pre-roll audio
- [ ] ACTIVE/IDLE conversation states
- [ ] `stay_silent` tool
- [ ] `require_wake_after_idle` fallback switch
- [ ] Mute switch with an indicator, plus the privacy baseline (no stored audio, notice to the room, opt-outs, consent record)

## Model comparison and benchmark

- [ ] 40 tool-calling turns plus 20 non-addressed utterances, 5 repeats each, against the decision rule: Groq 20b and 120b first through `bench/task_runner.py`, other candidates after purchases are approved
- [ ] Full-Duplex-Bench adapter (v1/v1.5, then v3)
- [ ] Our own task suite and team-recorded clips (including self-corrections)
- [ ] Acoustic stop-latency measurement
- [ ] Cost per successful task in every report

## Reliability and operations

- [ ] Network-loss detection with pre-synthesised fallback phrases
- [ ] Provider failover per stage, reconnect with backoff, health log
- [ ] systemd services with restart on failure, auto-start on boot, a watchdog
- [ ] Spoken notice when the daily cap is hit (today it just stops)
- [ ] Alert at $10 total spend

## Housekeeping

- [ ] Recheck Pipecat deprecations on every upgrade (ADR-003)
- [ ] Recheck prices on the day each provider goes live (several are promotional)
- [ ] Not yet researched: UP Diliman IP policy, Data Privacy Act details, most providers' terms on data use

## Done

- [x] Repo, pinned versions, CLAUDE.md, configs (ADR-001, ADR-002)
- [x] Cost ledger with hard daily caps; TTS metered from text (Aura-2 reports no usage)
- [x] Clock MCP server with annotated tools
- [x] Gate 1, scripted and live, over stdio and HTTP (ADR-004, ADR-005)
- [x] Step 4 barge-in truncation test: fake, fake_words, Aura-2 (ADR-006)
- [x] Step 5 duck-then-decide prototype (ADR-007)
- [x] Step 6 VoiceMCPBridge with stub voice servers (ADR-008)
