# Machine exercise: the filled 2-page deck

The text of the course's 2-page template (`machine_exercise_2page.pdf`), filled for VIA. The built slides are `docs/deck/machine-exercise.pptx` (editable) and `docs/deck/machine-exercise.pdf` (an image of each page). Every number comes from the Pi class-benchmark run on Nov 6, 2026 (`docs/class-benchmark.md`), unless a line names another run.

## Page 1: Full-Duplex Voice Agent on a Raspberry Pi

### 1. System diagram

Image: `docs/diagrams/architecture-current.png` (source `architecture-current.svg`).

- USB mic, WebRTC AEC, Silero VAD on the Pi, then the voice-listen MCP server (ASR), the Pipecat harness, and the voice-speak MCP server (TTS) to the USB speaker.
- Harness with MCP client: Pipecat 1.13.
- LLM: Claude Haiku 5.5 through the Anthropic API. Tools come from our own MCP servers.
- All twelve MCP servers are ours and run on the Pi: voice-listen, voice-speak, and ten task servers.
- Barge-in stops playback and cancels the LLM stream. The context keeps only the words that played (Cartesia word timestamps).
- The cloud boundary is the right-hand panel: Deepgram Flux, Anthropic and Cartesia, each behind a hard daily cap.

Flow strip: voice in, VAD, ASR, LLM (+tools), TTS, voice out. Barge-in: speech detected, stop TTS and LLM, listen.

### 2. Prompt (VIA v1, `harness/bot.py`, `PROMPT_VERSION`)

Excerpt:

> You are VIA, short for voice input agent, a hands-free voice assistant, and every reply is spoken aloud. Start with a short sentence of a few words, and keep the whole reply under about 30 words: no lists, formatting or parentheses. Call a tool straight away, with no words before it. If a tool result says waiting_for_undo or needs_confirmation, it has not happened yet: say so briefly and do not call the tool again. Speech recognition may mishear: act on the likely meaning, but if a short fragment could mean different things, ask in a few words and do not act.

Notes: stopping on barge-in is done by the harness, not asked of the model. Confirmation of costly or irreversible actions is enforced by `ToolPolicy` (undo window, read-back, spoken yes), so the prompt only tells the model what those results mean. The version is logged in every run's `run_start` event.

### 3. Context engineering

Goes in:

- System prompt (VIA v1) and the agent's persona from `configs/agents.yaml`
- Final transcripts from Flux (partials drive the face only)
- Flux end-of-turn events, eager at 0.5
- MCP tool results, and notes of what was done on the touch screen
- Document agents: the docs server's read tools (search, not a vector store)

Managed:

- `ContextWindow` keeps the last 20 user turns (ADR-047): 26 trims in a 41-turn run
- Prompt caching on the system prompt and latest turns: about 87% less LLM cost
- States: idle, listening, thinking, speaking, shown on the face
- On barge-in: audio queued for playback is dropped, and only heard words are committed
- Context size per run: prompt tokens median 6,766, max 8,467; messages median 44, max 84

## Page 2: MCP Services, Evaluation and Model Serving

### 4. MCP services and model serving

| Component | MCP server / transport | Tools | Model | Served where / notes |
|---|---|---|---|---|
| ASR | voice-listen / stdio, HTTP | `listen`, `stop_listening`, `status` | Deepgram `flux-general-en` | Pi server holds the key; audio on a Unix socket; Flux ends the turn |
| TTS | voice-speak / stdio, HTTP | `speak`, `stop`, `status` | Cartesia `sonic-3.6` | Pi server; streams chunks with word timestamps; cancelled on barge-in |
| LLM | harness MCP client (Pipecat 1.13) | function calling over all task tools | `claude-haiku-5-5` | Anthropic API; streaming tokens, prompt caching |
| VAD | in-process | speech start and stop | Silero VAD | Pi on-device, 16 kHz mono, start 0.08 s, stop 0.2 s |
| Task | clock, notes, lights, thermostat, music | `set_timer`, `add_note`, `set_light`, `set_thermostat`, `play_music` and more | none | Pi servers; lights and thermostat are mocks, music has Spotify |
| Task | knowledge, docs, workspace | `get_weather`, `fetch_content`, `search`, `read_file`, `write_file`, `bash` | Open-Meteo, web | Pi servers; files confined to the workspace, `bash` sandboxed |
| Task | comms, delegate | `send_message`, `call_contact`, `start_task` | Haiku 5.5 (delegate) | Pi servers; comms is a mock by default |

### 5. Evaluation and benchmark

Method (fd-benchmark stage test, the class's standard):

- One clock: the Pi's monotonic clock; every stage timestamped by fdbench's Pipecat observer
- VAD end, ASR final, LLM first token, TTS first audio, speaker
- E2E = speaker minus VAD end; barge-in = playback stopped minus speech start
- 10 phrases x 2 runs (20 turns), mean and p95, JSON log per run (`stage_latency.json`)

| Metric | Definition | Target | Mean | p95 |
|---|---|---|---|---|
| E2E latency | end of speech to audio out | < 4.0 s | 1.46 s | 1.96 s |
| ASR final | end of speech to transcript | < 0.8 s | 0.43 s | 1.18 s |
| LLM TTFT | transcript to first token | < 1.5 s | 0.53 s | 0.70 s |
| TTS TTFB | text to first audio chunk | < 0.6 s | 0.14 s | 0.18 s |
| Barge-in | speech to playback stopped | < 0.8 s | 0.70 s | 0.79 s (2 trials) |
| WER | ASR accuracy on the test set | < 15% | 5.5% | |

### Demo

A 2 to 3 minute script, in `docs/live-demo.md` under "Class demo on the Pi": a greeting, two live MCP tool calls (kitchen light, two-minute timer), a story cut off with "wait, stop", a slow weather call covered by the waiting phrase, a thermostat tap then a spoken question, and a note delete held and cancelled by voice. No push-to-talk at any point.

### Results

- Latency chart: fdbench's `latency_chart.svg`, run `2026-11-06_21-22-21`
- Raspberry Pi 5 Model B, 8 GB, active cooler with fan: peak 62.8 C, never throttled
- Network: Wi-Fi to the cloud, IPv6 off on the Pi's Wi-Fi; date Nov 6, 2026
- Models: Pipecat 1.13.0, Flux `flux-general-en`, Haiku 5.5, Cartesia `sonic-3.6`, Silero VAD, prompt VIA v1

### Challenges and references

On the slide this is a grid of one line per challenge (problem, cause, fix, proof). The fuller wording:

| Problem | Cause | Fix | Proof |
|---|---|---|---|
| Replies were slow: about 2.35 s from the end of the user's turn to her first audio (Mac, five class-benchmark rounds) | About 0.85 s for Flux to commit the end of turn, about 1.0 s to Haiku's first token, and about 0.5 s for the first sentence and Cartesia's first audio (ADR-046, from the Mac A/B runs) | Flux eager end of turn at 0.5, so the LLM starts on Flux's early guess, and a prompt rule for a short first sentence and a reply under about 30 words, so the first TTS request goes out sooner (ADR-046) | On the Pi run (`2026-11-06_21-22-21`, 20 turns): E2E 1.46 s mean, made of ASR final 0.43 s, LLM first token 0.53 s, TTS first audio 0.14 s and 0.36 s of hand-offs and playout. Eager alone measured 159 ms faster on chat turns and 355 ms on tool turns, with the same clips and clock for both arms |
| Choppy speech | Aura-2 audio arrived at 0.43x to 0.54x real time over the 279 ms round trip to Deepgram (ADR-014, ADR-015) | Cartesia as the TTS (ADR-015) | Median output gap 354 ms on Aura-2 (`20261031-163553-f4c9dd`) against 45 ms on Cartesia (`20261031-175305-d80a7c`) |
| The bot cut itself off when playing through speakers | It heard its own voice, and Pipecat has no echo cancelling | WebRTC AEC and a self-echo filter (ADR-013, ADR-042) | Three Mac speaker runs on Nov 5, 27 turns: no cut-off from the bot's own words. The filter never had to fire, so AEC did the work there |
| Turns stalled: 1.3 s and 7.1 s from speech end to end of turn, and the 7.1 s turn lost its reply | Smart Turn cannot end a Flux turn early, because Flux sends its final transcript only at its own end of turn | Flux owns the end of turn when it is the STT (`turn.stop: auto`, ADR-005) | Class benchmark, Mac, five rounds: 5 of 5 turns taken and 1 cut-in over 10 mid-sentence pauses |
| The prompt grew every turn, on top of about 8.4K tokens of system prompt and tools | The whole history was sent on every call | Keep the last 20 user turns, cut only before a user message so a tool call stays with its result (ADR-047) | Pi run: 26 trims in a 41-turn session; prompt tokens median 6,766, max 8,467 |
| Test runs lost to rate limits | Groq allows 8,000 tokens a minute, and an ME3 call is about 5,000 | Claude Haiku 5.5 as the live LLM (ADR-025) | Task suite: 61 of 64 passed (95.3%) with none lost, against 86 runs lost on Groq 20b |

Caveat on the first row: the 2.35 s and the 1.46 s are on different clocks. The Mac figure runs from the clip's annotated end of turn to her reply in the recording, on real Candor conversation; the Pi figure runs from the VAD end mark to reply audio handed to the output, on short clean phrases. On the recording clock the same Pi run is 1.71 s mean (1.57 s median), so part of the drop is the clock. Running the stage test on the Mac would separate the two, and needs Cartesia credits.

References: modelcontextprotocol.io; docs.pipecat.ai; github.com/quielq/quielq-full-duplex-voice-agent; github.com/trueshotjames/ai231-me3-full-duplex-benchmarking; run logs in `logs/` (not in the repo).
