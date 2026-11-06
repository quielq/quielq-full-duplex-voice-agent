# ME3 quick start on the Mac

Five steps. Steps 1 to 4 spend nothing. How the code fits together is in `docs/codebase.md`.

## 1. Install

You need [uv](https://docs.astral.sh/uv/). It installs Python 3.13 by itself.

```bash
uv sync
```

```bash
cp .env.example .env
```

## 2. Add your keys

Open `.env` and fill in the keys you have. Never commit it or paste a key anywhere.

| Key | For | Needed |
| --- | --- | --- |
| `DEEPGRAM_API_KEY` | Hearing you (Flux) | Yes |
| `ANTHROPIC_API_KEY` | The brain (Haiku 5.5) | Yes |
| `CARTESIA_API_KEY` | The voice | Yes |
| `OPENROUTER_API_KEY` | Document agents and the model picker | To use those |
| `TAVILY_API_KEY` | Web search | For real search |
| `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` | Music | For real music |

## 3. Check the machine

```bash
uv run python -m bench.doctor
```

Fix anything marked as failed. A warning about the built-in speakers is fine.

## 4. Try it for free

The face with no agent behind it:

```bash
uv run python -m harness.ui --demo
```

One typed request on fakes, with the face:

```bash
uv run python -m harness.bot --input text --say "turn on the kitchen light" --ui
```

Open [http://127.0.0.1:8770](http://127.0.0.1:8770) to see the face.

## 5. Run it live

This is a paid run. An 18 turn run on Nov 4 cost $0.21, and each provider stops at $5 a day.

```bash
ME3_WORKSPACE_LIVE=1 ME3_TOOL_STAGES=search=tavily,delegate=anthropic uv run python -m harness.bot --stages stt=deepgram_flux,llm=anthropic,tts=cartesia,music=spotify --echo-cancel --ui
```

Open [http://127.0.0.1:8770](http://127.0.0.1:8770) and start talking. Press Ctrl-C to stop.

- Headphones work best. On the laptop speakers, keep `--echo-cancel`.
- Pick an agent on the face. Choosing another one starts a new session with it.
- Leave out `music=spotify` if you have not set up Spotify (below).
- `ME3_WORKSPACE_LIVE=1` makes `bash`, `serve` and `open_in_browser` real (sandboxed, each change asks first). `delegate=anthropic` runs background tasks such as "make a deck" on Haiku 5.5 under the `anthropic` cap. Without them these tools are mocks: a task reports done but makes nothing, and the browser never opens.

To route speech through our own MCP voice servers instead, change the stages to `stt=voice_listen,tts=voice_speak`. It sounds the same, and the keys stay in the servers.

## Try saying

| Say | What happens |
| --- | --- |
| "What time is it?" | The real time |
| "Set a timer for one minute" | It shows on the face and is said aloud when due |
| "Turn on the kitchen light" | The simulated light changes. You can also tap it |
| "What's the weather in Quezon City?" | Real weather |
| "Search for the latest news on Mars" | Real web search |
| "Build a small website about red pandas" | Writes files to `data/workspace`, then offers to open it |
| "Delete the timer" | Waits 5 seconds. Say "cancel" to stop it |
| "Stop" while it talks | It goes quiet at once |

## What is real

| Tools | In the step 5 command |
| --- | --- |
| Clock, notes, documents, weather, web pages, files | Real |
| Web search, music | Real, with your keys |
| `bash`, `serve`, `open_in_browser` | Real and sandboxed. Each asks for a spoken yes |
| Messages and calls | Mocks (see below) |
| Lights, thermostat | Simulated, shown on the face |
| `send_file` | Always a mock |

## Optional setup

**Spotify** (needs Premium). Register `http://127.0.0.1:8888/callback` in your Spotify developer app, run this once, and open the Spotify app before a run. Music plays there.

```bash
uv run python -m bench.spotify_login
```

**Your documents.** Put files in `data/documents`. The agent can read them but never change them. The document agents use the samples, which are copied in on the first run.

**Real messages and calls.** Follow "Messages and calls through the Mac bridge" in `docs/tools.md`, then add `comms=bridge` to `ME3_TOOL_STAGES`.

## If something goes wrong

These spend nothing. Start with the doctor, then read the last run:

```bash
uv run python -m bench.doctor
```

```bash
uv run python -m bench.run_report --live
```

| Problem | Fix |
| --- | --- |
| "refused: output is MacBook Air Speakers" | Add `--echo-cancel`, or use headphones |
| It answers its own voice | Use headphones, or turn the volume down |
| The face says "Agent not running" | Start the step 5 command with `--ui` |
| Port 8770 is busy | Stop the other run or demo |
| A tool says it is a mock, or a task says done but no file appears | Check `ME3_WORKSPACE_LIVE=1` and `ME3_TOOL_STAGES=search=tavily,delegate=anthropic` are in the command |
| Wrong mic or speaker | List devices with `uv run python -m harness.audio_io`, then set `audio.input_device` and `audio.output_device` in `configs/mac.yaml` |
| It says it hit a limit | A daily cap was reached. Check with `uv run python -m bench.cost_ledger` |
| "Now playing" but no sound | Open the Spotify app first |

For a scripted demo, see `docs/live-demo.md`.
