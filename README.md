# ME3: Via, a full-duplex voice agent on a Raspberry Pi

Via ("voice input agent", said "vee-yah") is a hands-free, English-only voice assistant built for AI222 (UP Diliman MEng AI). You talk to it without a wake word or a button. It can be interrupted mid-sentence, and it does real work through MCP tools: timers, notes, lights, the thermostat, music, the web, files, documents, and background tasks.

It runs on a Raspberry Pi 5 with a USB mic, a USB speaker and a 7-inch touch screen, and on a MacBook with headphones for development.

## What runs where

- **On the device:** the Pipecat 1.13 harness, Silero VAD, echo cancelling, audio in and out, the tool policy, the robot face, and every MCP server. Those are the ASR and TTS voice servers (`voice-listen`, `voice-speak`) and ten task servers.
- **In the cloud:** speech to text (Deepgram Flux), the LLM (Claude Haiku 5.5), and text to speech (Cartesia). Each one has a hard daily spending cap.

Barge-in stops playback and the LLM stream, and the context keeps only the words that were actually played. The diagram and design are in `docs/architecture.md` and `docs/codebase.md`.

## How it performs

The class benchmark (fd-benchmark) on the Pi 5, Nov 6, 2026. Means, with p95 in brackets:

| Stage | Result | Class target |
|---|---|---|
| End of speech to reply audio | 1.46 s (1.96 s) | under 4.0 s |
| Speech recognition final | 0.43 s (1.18 s) | under 0.8 s |
| LLM first token | 0.53 s (0.70 s) | under 1.5 s |
| TTS first audio | 0.14 s (0.18 s) | under 0.6 s |
| Barge-in, speech to playback stopped | 0.70 s (two trials) | under 0.8 s |
| Word error rate | 5.5% | under 15% |

Method, full results and the comparison with GPT-4o Realtime, Gemini Live, Moshi and others are in `docs/class-benchmark.md`.

## Try it with no spending

Every provider defaults to a free fake, so this needs no keys. Python 3.13 and [uv](https://docs.astral.sh/uv/) are required.

```bash
uv sync
uv run pytest -q
uv run python -m harness.bot --input text --mcp stdio
uv run python -m harness.ui --demo
```

The last command shows the robot face at http://127.0.0.1:8770.

Running it live needs your own provider keys in `.env` (start from `.env.example`). The steps are in `docs/quickstart-mac.md` and `docs/quickstart-pi.md`.

## Where to read next

| Document | What it covers |
|---|---|
| `docs/architecture.md` | Design, what was planned against what was built |
| `docs/codebase.md` | Layout, how one spoken turn moves through the code |
| `docs/switches.md` | Every component that can be turned on or off, and its default |
| `docs/tools.md` | The MCP tools and their guardrails |
| `docs/class-benchmark.md` | Benchmark results |
| `docs/decisions/` | Architecture decision records, ADR-001 onward |
| `docs/privacy.md` | What is recorded and what never leaves the device |
