# ME3 live demo script

A spoken run of about 6 minutes on the Mac. It shows the tools, the guardrails, the skips (reply templates and track skip), and the turns (end of turn, barge-in, held actions).

This is a paid run, about $0.20 (an 18 turn run on Nov 4 cost $0.21, mostly Cartesia).

## Before you start

Messages are mocks in this run, but the mock still needs a saved contact. The example file has one placeholder, Ana, and nothing is sent:

```bash
cp configs/contacts.example.yaml data/contacts.yaml
```

```bash
uv run python -m bench.doctor --quick
```

Open the Spotify app, then start the agent. `--reply-templates` applies to this run only. The default stays off (ADR-020).

```bash
ME3_WORKSPACE_LIVE=1 ME3_TOOL_STAGES=search=tavily,delegate=anthropic uv run python -m harness.bot --stages stt=deepgram_flux,llm=anthropic,tts=cartesia,music=spotify --echo-cancel --ui --reply-templates
```

Show the face at [http://127.0.0.1:8770](http://127.0.0.1:8770) and wait for it to look ready.

## 1. Tools and skips

A skip means a simple tool's reply comes from a fixed template, so the second LLM call never happens. Wording in quotes under "Expect" is fixed by the template.

| Say | Expect | Shows |
| --- | --- | --- |
| "What time is it?" | "It's ..." with the time | `get_time`, skip |
| "Set the thermostat to 22." | "Thermostat set to 22 degrees." The panel changes | Mock tool, skip |
| "Turn on the kitchen light and set the thermostat to 24." | Both change, in the model's own words | The word "and" keeps the LLM, so the second tool is not lost |
| "Add a note: bring the slides." | "Noted." | Real note, skip |
| "Read my notes." | The model reads them out | No template, so the LLM writes the reply |
| "What's the weather in Quezon City?" | "It's ..." with the real weather | Real API, skip. This took 12 s in rehearsal, so fill the gap |
| "Search for the latest news on Mars." | A short spoken summary | Real Tavily search |

## 2. Music

Speak up, since the music is playing while you talk.

| Say | Expect | Shows |
| --- | --- | --- |
| "Play some jazz." | Plays in the Spotify app | `play_music` |
| "Skip this song." | The next track starts | `next_track` |
| "Pause the music." | "Music paused." | `stop_music`, skip |

## 3. Turns

| Say | Expect | Shows |
| --- | --- | --- |
| "Set a timer for..." (stop for one second) "...ten minutes." | One reply, after the whole sentence | Flux waits on an unfinished thought |
| "Tell me a long story about a robot." Then, a few seconds in: "Stop." | It goes silent almost at once | Barge-in |
| "What was the last thing you said?" | It names where it was cut off, not the end of the story | Context holds only the words you heard (Cartesia word timestamps) |
| "Tell me about the history of Manila." Then, midway: "Actually, what time is it?" | It drops Manila and gives the time | A new request takes over the turn |

## 4. Guardrails

Wait for each question to finish before you answer. A yes only counts after the question has played.

| Say | Expect | Shows |
| --- | --- | --- |
| "Delete the ten minute timer." | It says the timer goes in 5 seconds unless you say cancel | Undo window, nothing has run yet |
| "Stop." | "Okay, I stopped that. Nothing changed." | The harness answers, with no LLM call |
| "Text Ana that I'm running late." | "Send Ana this message: ... Should I send it?" | The read-back comes from the real arguments, not the model |
| "Yes." | "Okay. Say cancel in the next 5 seconds to stop it." Then wait | Yes, then undo. A mock, so nothing is sent |
| "Use bash to list the files in the workspace." | "Run this command: ... Should I run it?" | Sandboxed shell, read-back |
| "Yes." | It reports the files | Runs in `sandbox-exec` |
| "Use bash to run sudo reboot." | A refusal, with no question asked | The shell guard denies it. A yes cannot lift this |

Say the `sudo` line once. Three critical refusals lock the shell and messaging until the session ends. The model may also decline before it calls the tool, which is a refusal from the model, not the guard.

Press Ctrl-C to stop.

## Afterwards

These spend nothing.

```bash
uv run python -m bench.run_report --live
```

```bash
uv run python -m bench.cost_ledger
```

In the timeline, a skipped turn has one `llm_request` and a normal tool turn has two. Add `--all` to the report to see the `tool_policy` event with `template_reply`. A cut-off turn shows as `interrupted` under Turns.

## Optional: backchannels

The default barge-in stops on any speech, so "mm-hmm" cuts the agent off. To show it surviving, run the same command with `--barge-in duck`, ask for a story, and say "Mm-hmm." The agent dips in volume and keeps talking. "Stop" still works, but took about 0.9 s to silence when measured (ADR-007). This is a prototype and off by default.

## If it goes wrong

| Problem | What to do |
| --- | --- |
| It says Ana is not a saved contact | `data/contacts.yaml` is missing. The first step covers it |
| It says the shell is a mock, or the browser never opens | `ME3_WORKSPACE_LIVE=1` is missing from the command |
| A task says done but no file appears | `delegate=anthropic` is missing from `ME3_TOOL_STAGES` |
| A cough or room noise cuts off a reply | Expected in the default barge-in mode. Ask again |
| A hold answer is ignored | The yes came before the question ended. Ask for the action again |
| Anything else | `uv run python -m bench.doctor`, then the table in `docs/quickstart-mac.md` |

## On the Pi

Use `--profile pi`, drop `--echo-cancel`, since `pi.yaml` already turns it on, and add `--spotify-player` so music plays in the face (ADR-031). Check that the VCM services are not holding the mic. The bash lines have not run under `bwrap` on a Pi, so try them before showing them.

## Class demo on the Pi (2 to 3 minutes)

The machine exercise asks for a real task, natural turns with no push-to-talk, a barge-in, a live MCP tool call, and recovery when an API is slow. This run shows all five on the Pi with the face on the 7-inch screen, and ends with a real iMessage sent through the Mac phone bridge. It costs about 1,500 Cartesia characters and under $0.05 for the rest, so a rehearsal and the take fit easily.

### Before you start (on the Pi)

1. After any reboot, check IPv6 on the Wi-Fi. If `ip -6 addr show wlan0` prints a `2001:` address, turn it off, or Cartesia will time out (`docs/quickstart-pi.md`):
   ```bash
   sudo sysctl -w net.ipv6.conf.wlan0.disable_ipv6=1
   ```
2. Check the machine and that VCM is not holding the mic:
   ```bash
   cd ~/quielq-full-duplex-voice-agent && ~/.local/bin/uv run python -m bench.doctor --quick
   ```
3. On the Mac, on the same Wi-Fi, start the phone bridge for the real message at 2:20. Keep the Mac awake and Messages signed in. Get the Pi's address with `hostname -I` on the Pi, and check the link from the Pi as in `docs/quickstart-pi.md`, step 5:
   ```bash
   uv run --env-file .env python scripts/mac_phone_bridge.py --host 0.0.0.0 --allow-from <pi ip>
   ```
4. Start VIA through both voice servers, with the face and real messages:
   ```bash
   ME3_WORKSPACE_LIVE=1 ME3_TOOL_STAGES=search=tavily,delegate=anthropic,comms=bridge ~/.local/bin/uv run python -m harness.bot --profile pi --stages stt=voice_listen,llm=anthropic,tts=voice_speak --ui
   ```
5. Open the face full screen with `scripts/pi_kiosk.sh` (`WAYLAND_DISPLAY=wayland-0`). Wait until the chip shows "VIA: Generalist" and the status says Listening. Start recording.

### The script

Speak normally and wait for each reply unless the line says otherwise. The times are a guide.

| Time | You say or do | What it shows |
|---|---|---|
| 0:00 | "Hi VIA, what can you help me with?" | Natural turn: Flux ends the turn, no button, a short spoken answer |
| 0:15 | "Turn on the kitchen light." | Live MCP tool call: `set_light` on the lights server, and the light turns on in the face |
| 0:30 | "Set a timer for two minutes." | A second tool: `set_timer` on the clock server, and the timer appears in the face |
| 0:45 | "Tell me a short story about a robot on a rice farm." Two sentences in, say "Wait, stop." | Barge-in: playback stops within about 0.7 s and the LLM stream is cancelled. The context keeps only the words that played |
| 1:05 | "Actually, what's the weather in Baguio right now?" | A slow API: `get_weather` is a slow tool, so the waiting phrase ("One sec") plays at once from a stored clip, then the answer |
| 1:30 | Tap the thermostat's Warmer button twice on the screen, then say "What's the thermostat set to now?" | Screen and voice share one policy: the taps go through `ToolPolicy`, and the model reads the new value with a tool, not from memory |
| 1:50 | "Add a note: bring the Pi charger to class." Then "Delete that note." When it asks, say "cancel." | Guardrails: a delete is held with an undo window and released only by your words, so "cancel" keeps the note |
| 2:20 | "Text <contact> that the demo is running." When it reads the message back, say "Yes." Then wait 5 seconds | A real action leaves the Pi: the harness reads back the real arguments, holds the send for 5 seconds, then the Mac bridge sends an iMessage. Show it arriving on the phone |
| 2:45 | "Thanks VIA, that's all for now." | Closing turn. Stop the recording, then stop the bot with Ctrl+C |

Pick `<contact>` from `data/contacts.yaml` on the Pi and tell that person a test text is coming. A call ("Call <contact>.") also works, but someone must click Call on the Mac, so it is optional.

If a reply is slow to start, keep going: the waiting phrase covers it. If a cough or room noise cuts a reply off, that is barge-in working; ask again.

### Afterwards

```bash
~/.local/bin/uv run python -m bench.run_report --live
```

Note the run id for the deck's demo box. Keep the video out of the repo: it is a recording of a person (CLAUDE.md rule 3).
