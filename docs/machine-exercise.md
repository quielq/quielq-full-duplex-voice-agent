# Machine exercise: the filled 2-page deck

The text of the course's 2-page template (`machine_exercise_2page.pdf`), filled for Via. The built slides are `docs/deck/machine-exercise.pptx` (editable) and `docs/deck/machine-exercise.pdf` (an image of each page). Every number comes from the Pi class-benchmark run on Nov 6, 2026 (`docs/class-benchmark.md`), unless a line names another run.

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

### 2. Prompt (Via v1, `harness/bot.py`, `PROMPT_VERSION`)

Excerpt:

> You are Via, short for voice input agent, a hands-free voice assistant, and every reply is spoken aloud. Start with a short sentence of a few words, and keep the whole reply under about 30 words: no lists, formatting or parentheses. Call a tool straight away, with no words before it. If a tool result says waiting_for_undo or needs_confirmation, it has not happened yet: say so briefly and do not call the tool again. Speech recognition may mishear: act on the likely meaning, but if a short fragment could mean different things, ask in a few words and do not act.

Notes: stopping on barge-in is done by the harness, not asked of the model. Confirmation of costly or irreversible actions is enforced by `ToolPolicy` (undo window, read-back, spoken yes), so the prompt only tells the model what those results mean. The version is logged in every run's `run_start` event.

### 3. Context engineering

Goes in:

- System prompt (Via v1) and the agent's persona from `configs/agents.yaml`
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
- Models: Pipecat 1.13.0, Flux `flux-general-en`, Haiku 5.5, Cartesia `sonic-3.6`, Silero VAD, prompt Via v1

### Challenges and references

| Challenge | Fix | Measurement |
|---|---|---|
| Aura-2 audio arrived slower than real time, with gaps mid-reply, and has no word timestamps | Cartesia as the TTS (ADR-015) | Median output gap 354 ms to 45 ms; context keeps only heard words |
| Waiting for the end of the turn | Flux eager end of turn at 0.5 (ADR-046) | 159 ms faster on chat turns, 355 ms on tool turns |
| Cartesia timed out on the Pi | IPv6 off on Wi-Fi (the route did not work) | Handshake 0.39 s over IPv4 against a 10 s timeout; then 30 of 30 trials spoke |
| Pi under-voltage | A proper 5 V supply | `throttled=0x0` through the run, peak 62.8 C |
| Bot answering its own voice on speakers | WebRTC AEC and a self-echo filter (ADR-013, ADR-042) | 27 speaker turns with no self-interruption |

References: modelcontextprotocol.io; docs.pipecat.ai; github.com/quielq/quielq-full-duplex-voice-agent; github.com/trueshotjames/ai231-me3-full-duplex-benchmarking; run logs in `logs/` (not in the repo).
