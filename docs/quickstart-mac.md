# ME3 quick start on the Mac

This gets the full agent running on a Mac: live voice, the robot face, and every tool that can be real. Steps 1 and 2 are needed once. After that, step 3 is all you run.

How each tool works and why it is guarded is in `docs/tools.md`.

## 1. Install

You need Python 3.13 through [uv](https://docs.astral.sh/uv/). uv installs the right Python by itself.

```bash
uv sync
```

```bash
cp .env.example .env
```

Fill these in `.env`. Never commit this file, and never paste a key into chat or a log.

| Key | Used for | Needed |
| --- | --- | --- |
| `DEEPGRAM_API_KEY` | Hearing you (Flux) | Yes |
| `ANTHROPIC_API_KEY` | The brain (Haiku 5.5) | Yes |
| `CARTESIA_API_KEY` | The voice | Yes |
| `TAVILY_API_KEY` | Web search | For real search |
| `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` | Music | For real music |
| `ME3_PHONE_BRIDGE_TOKEN`, `ME3_PHONE_BRIDGE_URL` | Messages and calls | For real messages |

Then check the machine. This spends nothing.

```bash
uv run python -m bench.doctor
```

A warning about built-in speakers is normal. Step 3 handles it.

## 2. Optional setup, once

**Spotify.** Needs Premium. In your Spotify developer app, register `http://127.0.0.1:8888/callback`, then log in:

```bash
uv run python -m bench.spotify_login
```

**Your documents.** Put files in `data/documents`. The agent can read and search them but never change them.

**Messages and calls.** Follow "Messages and calls through the Mac bridge" in `docs/tools.md`. It takes a token, a contacts file, and some Apple settings.

## 3. Run it

Open the Spotify app first if you want music. Audio plays there, not through the agent.

```bash
ME3_WORKSPACE_LIVE=1 ME3_TOOL_STAGES=search=tavily uv run python -m harness.bot --stages stt=deepgram_flux,llm=anthropic,tts=cartesia,music=spotify --echo-cancel --ui
```

Then open the face at [http://127.0.0.1:8770](http://127.0.0.1:8770) and start talking. Press Ctrl-C to stop.

- Headphones give the cleanest result. On speakers, `--echo-cancel` must stay on, or the run is refused.
- This is a paid run. A 4 to 5 minute run on Nov 2 cost about $0.16, and each provider has a daily cap of $5.
- `ME3_TOOL_STAGES` is set in the command so it replaces any value in `.env`.

To include real messages and calls, start the bridge in a second terminal:

```bash
uv run --env-file .env python scripts/mac_phone_bridge.py --dry-run
```

Then change the setting in the run command to `ME3_TOOL_STAGES=search=tavily,comms=bridge`. `--dry-run` sends nothing. Remove it when you want real sends.

## 4. Things to try

| Say | What happens |
| --- | --- |
| "What's the weather in Quezon City?" | Real weather |
| "Set a timer for one minute" | The timer shows on the face, and the agent speaks when it is due |
| "Turn on the kitchen light" | The simulated light changes on the panel |
| "Set the thermostat to 22" | The simulated thermostat changes on the panel |
| "Play some jazz" | Plays in the Spotify app |
| "Search for the latest news on Mars" | Real web search |
| "Plan and build a small website about red pandas" | Writes the files to `data/workspace`, then offers to open the page |
| "Yes" after a question | Confirms a held action. Say "cancel" to stop one |

## What is real and what is not

| Tools | Status in the step 3 command |
| --- | --- |
| Clock, notes, documents, weather, web pages, files | Real |
| Web search, music | Real, using your keys |
| `bash`, `serve`, `open_in_browser` | Real and sandboxed. Each asks for a spoken yes |
| Messages and calls | Mock until you add `comms=bridge`. A call also needs a click on the Mac |
| Lights, thermostat | Simulated, shown on the panel |
| `send_file` | Always a mock. Files go to `data/send_outbox` |

## Try it without spending

```bash
uv run python -m harness.ui --demo
```

This shows the face cycling through its expressions at the same address, with no bot. This one runs a typed request on fakes:

```bash
uv run python -m harness.bot --input text --say "turn on the kitchen light" --ui
```

## If something goes wrong

Run these first. Neither spends anything.

```bash
uv run python -m bench.doctor
```

```bash
uv run python -m bench.run_report
```

| Problem | Fix |
| --- | --- |
| "refused: output is MacBook Air Speakers" | Add `--echo-cancel`, or use headphones |
| The agent answers itself | Use headphones, or lower the speaker volume |
| The face says "Agent not running" | Start the step 3 command, and check it has `--ui` |
| Port 8770 is busy | Stop the other run or demo, or change `ui.port` in `configs/mac.yaml` |
| "Now playing" but no sound | Open the Spotify app, and check it is the active device |
| A tool says it is a mock | Check `ME3_WORKSPACE_LIVE=1` and `ME3_TOOL_STAGES` are in the command |
| The wrong mic or speaker is used | List names with `uv run python -m harness.audio_io`, then set `audio.input_device` and `audio.output_device` in `configs/mac.yaml` |
| The agent stops and says it hit a limit | A daily cap was reached. Check with `uv run python -m bench.cost_ledger` |
