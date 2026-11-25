# ME3: VIA, a full-duplex voice agent on a Raspberry Pi

VIA ("voice input agent", said "vee-yah") is a hands-free, English-only voice assistant built for AI222 at UP Diliman (MEng AI). You talk to it with no wake word and no button, you can cut it off mid-sentence, and it does real work through MCP tools.

It runs on a Raspberry Pi 5 with a USB mic, a USB speaker and a 7 inch touch screen, and on a MacBook with headphones for development.

## What it can do

| Ask it to | What happens |
|---|---|
| Build a website, then publish it | It researches with web search, writes the site into its workspace, checks it, puts it on GitHub Pages after a spoken yes, and opens the live link. The face shows the project, its files and the link |
| Look you up | It knows its owner from `configs/owner.yaml`, hears the name correctly, and searches for it |
| Answer from your documents | It searches `data/documents`, reads the best match, and names the document it used. Document agents answer only from their own folders |
| Run the house | Timers, alarms, notes, lights, thermostat, music, weather, web pages |
| Send a message | Reads it back, waits for "yes", then sends an iMessage through the Mac |
| Run a command | A sandboxed shell: reads run at once, changes ask first, and dangerous commands are refused |

Every action that changes something outside the workspace asks first. The question is built from the real arguments, never from the model's words, and only your voice can answer it. Most actions also wait 5 seconds so you can say "cancel".

## What runs where

- **On the device:** the Pipecat 1.13 harness, Silero VAD, echo cancelling, audio in and out, the tool policy, the robot face, and every MCP server: the two voice servers (`voice-listen`, `voice-speak`) and ten task servers.
- **In the cloud:** speech to text (Deepgram Flux), the LLM (Claude Haiku 5.5), and text to speech (Cartesia). Each one has a hard daily spending cap.

When you cut VIA off, playback and the LLM stream stop, and the context keeps only the words you actually heard. `docs/codebase.md` follows one spoken turn through the code, with a diagram.

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

The method, the full results, and the comparison with GPT-4o Realtime, Gemini Live, Moshi and others are in `docs/class-benchmark.md`.

## Try it with no spending

Every provider defaults to a free fake, so this needs no keys. You need Python 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

```bash
uv run pytest -q
```

```bash
uv run python -m harness.bot --input text --say "set a timer for one minute"
```

```bash
uv run python -m harness.ui --demo
```

The last command shows the robot face at http://127.0.0.1:8770, walking through a build, a publish question, the undo countdown, and the published link.

## Run it live

Put your own keys in `.env` (start from `.env.example`), then follow `docs/quickstart-mac.md`. It takes you from the first live turn to building and publishing a site by voice. For the Pi, follow `docs/quickstart-pi.md`.

## Where to read next

| Start here | |
|---|---|
| `docs/quickstart-mac.md` | Run it on a Mac, what to say, and what to do when something goes wrong |
| `docs/quickstart-pi.md` | Set up the Pi, in order |
| `docs/live-demo.md` | Scripted demos, including build, publish and open |

| How it works | |
|---|---|
| `docs/codebase.md` | Layout, how one spoken turn moves through the code, the key code |
| `docs/tools.md` | Every MCP tool, its guardrails, and how to add one |
| `docs/switches.md` | Every component that can be turned on or off, and its default |
| `docs/architecture.md` | The design review, with an "As built" summary at the top |
| `docs/decisions/` | Architecture decision records, ADR-001 onward. Each says what was decided, why, and what else was tried |

| Results and context | |
|---|---|
| `docs/class-benchmark.md` | Benchmark method and results |
| `docs/research.md` | The provider and licence research behind the design |
| `docs/privacy.md` | What is recorded, and what never leaves the device |
| `TODO.md` | Open work |
