# ME3 to-do list

Living list of open work. Tick items as they close, add new ones where they belong, and note the ADR or commit that settled each one. Last updated: Nov 5, 2026.

Debugging: start with `uv run python -m bench.doctor`, then `uv run python -m bench.run_report --live`.

Status as of Nov 4, evening: the agent runs live on the Pi with Flux, Haiku 5.5, Cartesia, the face, and Spotify in the page (last Pi run `20261102-123315-a8d32d`). On Nov 4 the voice servers (ADR-032), the rotating thinking phrase (ADR-033), our own command policy (ADR-034), the `delegate` server (ADR-035), the audio-sample latency method (ADR-036), network-loss recovery (ADR-037), and voice server reconnects were built on `build/voice-servers`, and every one has now run live on the Mac at least once. The full suite passed after the merges (643 passed, 3 skipped), plus one test since. Main matches `build/voice-servers` and is pushed to GitHub (Nov 4, after the live runs and these to-do updates). Today's spend is about $0.38. Feature freeze is Nov 5 and the deadline is Nov 7.

## Next, in order (Nov 4)

Done on the Mac, Nov 4: the live run through both voice servers (`20261104-202417-64248a`, headline p50 3.0 s, no-tool 2.8 s, tool 4.4 s, ADR-036), the Cartesia interruption bench direct and through `voice-speak` (over-commit 1 word), `delegate` from a script and by voice, the recovery clips, and the `bash` delete read-back after a prompt fix (`20261104-204433-6a77ad`).

1. On the Pi, in this order: the voice stages, the hop probe, the echo and barge-in test, and live `bash` in `bwrap`. Then switch the boot service to the voice stages.
2. The latency benchmark (spec item 6, graded): the method is built on fakes (ADR-036). Run `bench.latency_bench` live for 30 or more kept turns, then add cost per successful task.
3. Pi cooling and power before the demo.
4. Small fixes found live on Nov 4, below under "Found in the Nov 4 live runs".

## Waiting on Quiel

- [x] Give the go for step 7, the Pi port (Nov 2)
- [x] Choose how to build the voice servers: option B, thin servers that hold the Flux and Cartesia connections, audio on a Unix socket, playback and echo cancelling kept in the harness (Nov 4). Built, ADR-032
- [x] `delegate` model: Haiku 5.5 for now. OnIt is dropped (Nov 3 and 4). Built on fakes, ADR-035
- [x] Patent question dropped, and the repo goes public as a course requirement (Nov 3). Privacy measures are listed in `docs/privacy.md`
- [x] Reply templates stay off: the second LLM call always runs (Nov 4, ADR-020). A natural thinking phrase covers long waits instead
- [x] Our own command policy replaces the vendored OnIt file (Nov 4, ADR-034): reads run at once, changes and runs ask, simple syntax only, installs refused clearly, unlisted programs ask, deletes copied to `.versions` first, plain read-backs with the exact command on screen
- [x] The live Mac run through both voice servers (Nov 4, `20261104-202417-64248a`, $0.21)
- [x] The Cartesia bench through `voice-speak`: over-commit 1, stop 277 ms, same as direct (Nov 4, `20261104-202147-ab3d44`, $0.0246)
- [x] Try the new read-backs live once (Nov 4, `20261104-204433-6a77ad`): after the prompt fix, `rm -r` was read back in plain words, ran after a yes, and kept a copy; a restore with `cp -r` was read back too; `ls -la` ran at once
- [ ] Decide whether to turn off Pipecat's empty turn recovery with addressing off too: it spends on noise (ADR-016)
- [ ] Approve a first real message and call through the phone bridge (ADR-024)
- [x] Generate the five waiting phrase clips through voice-speak (Nov 4, $0.0029, the first live voice-speak run)
- [ ] Later purchase: OpenRouter ($5), only if the model comparison goes ahead
- [x] Approve ADR-009 and the Oct 31 replan (Oct 31)
- [x] Confirm the deadline: Nov 7 holds (Oct 31)
- [x] Approve a Cartesia cap and voice: $5.00/day, public Skylar voice (Nov 1)
- [x] Choose the LLM: Haiku 5.5, replacing the 20b and 120b choice (ADR-025)
- [x] Try the live mic: Flux, Haiku, and Cartesia, five mic runs on Nov 1 and 2
- [x] Set up the Spotify developer app (ADR-022) and fund Anthropic (ADR-025)
- [x] Buy Cartesia Pro (confirmed Nov 2)
- [x] Command list: follow the tools and servers already requested, no separate list to check (Nov 2)
- [x] Pi audio hardware: separate USB mic and USB speaker, not a speakerphone (ADR-029)

## Found in the Nov 4 live runs

- [x] Haiku refused to delete with `bash`, saying it would be permanent, so the read-back never ran. The `bash` description and prompt now say a delete asks first and keeps a copy (86c71f5), and the retry worked
- [x] Echo on the Mac speakers: the bot heard a few of its own words once ("I can't delete"), cut itself off, and answered them. With echo cancelling on, its own recent words that cut it off are no longer answered (Nov 5, ADR-042, fakes only). Live Nov 5: three speaker runs, 27 turns, no self-echo at all, so the guard had nothing to drop The cut-off itself remains: headphones on the Mac, and the Pi echo test
- [x] The thinking phrase played on every tool turn and delayed quick tool replies by about 0.85 s. A quick tool now restarts the wait at 2000 ms (Nov 5, ADR-033). Live Nov 5: five quick tool turns had no clip and first audio at 1.9 s
- [x] DeepSeek said "I'll check your documents" before a tool call. A waiting phrase before a tool call is now dropped before the TTS (Nov 5, ADR-041). Live Nov 5: Haiku said two, both dropped
- [x] Asked to list the workspace, Haiku's reply named only the `delegate_trial` files though the tool returned all of them. `list_files` now adds a count per top-level folder for each root (Nov 5, ADR-021). Live Nov 5: the result carried the folder counts; the spoken reply was not captured
- [ ] Cost: the 18-turn run was $0.21, about $0.012 a turn, mostly Cartesia. Each Haiku call sends about 8,400 prompt tokens, so prompt caching (Latency, below) would help
- [ ] The end-of-turn wait is the largest part of the headline at 1.3 s p50 (ADR-036)

## Found in the Nov 5 live runs

Mac speakers with `--echo-cancel`, runs `20261105-195338-eb8cc9`, `20261105-195709-7ed5c6` (DeepSeek), `20261105-195758-061828`, 27 turns, $0.28 (plus $0.03 for `20261105-194831-817d86`). Headline p50 3.1 s (no-tool 2.5 s, tool 3.5 s, 17 measured turns, ADR-036).

- [x] `get_weather` for a misheard place ("Caddon City") never returned: no `tool_call_end`, no timeout event, and nothing was said for 11 s until the user asked again. Now one 9 s deadline with side by side lookups, and `tool_call_cancelled` and `tool_no_end` in the log and report (ADR-024, fakes only)
- [x] After a `next_track` tap on the face, Haiku named the old song from context instead of calling `get_now_playing`. Taps now reach the model at the start of the next turn, and `get_now_playing` says never to answer from memory (ADR-043, fakes only)
- [ ] "wait" was heard as "Great." The bot stopped, then answered it as a new turn ("Glad that helped")
- [ ] A short "Okay." over the bot's reply cuts it off, and it restarts ("Sorry, my last reply cut off"). Belongs with backchannel handling in Barge-in
- [ ] Misheard names: "UP Diliman" as "Yuki Deleman", "Mayon Builders" as "my on builders". On the second, DeepSeek answered without a docs tool call
- [ ] The face server printed an aiohttp "Missing return statement on request handler" traceback at exit
- [ ] Weather turns took 4.5 to 6.1 s to first audio, the slowest kind of turn in these runs

## Robot face (ADR-027)

- [x] Browser page with the four expressions from the artwork, plus listening, thinking, working, asking, and muted (`--ui`, `harness/ui.py`, `harness/ui_page.html`)
- [x] Blink, talking mouth, current step, captions, and a sad face when the agent is down
- [x] Lights, thermostat, music, and timers panel, with a touch mute button
- [x] Layout checked at 800x480 on the Mac with `uv run python -m harness.ui --demo`
- [x] Try it on the live mic: used on the Pi through three live sessions, Nov 2
- [x] Confirm the display's resolution: 800x480 on DSI-2 (`wlr-randr`, Nov 2)
- [x] Light and thermostat taps and a music button on a live run (Nov 5, `20261105-195758-061828`): each went through `run_from_screen` and applied
- [x] Model picker on a live run (Nov 5): Haiku to DeepSeek V4.1 Flash and back, each switch starts a new run
- [ ] Chromium kiosk start on the Pi, and CPU cost of the page next to the agent
- [ ] Decide what `open_in_browser` does on the Pi: it covers the face, and touch has no way back
- [ ] Optional: touch control of lights and thermostat (needs an ADR, it must not get around the tool policy)

## Pi port, found on Nov 2 (ADR-029, ADR-030, ADR-031)

- [ ] Cooling: the Pi reached 84 C on Nov 2 and has throttled since boot. A fan clashes with the display mounting, so find what fits, and check whether open page tabs drive the heat
- [ ] Power: `throttled=0xf0000` includes under-voltage, so check the supply
- [ ] Barge-in over the bot's speech and the 60 s echo test, live on the Pi. The clean bench talker test failed before the mic gain was lowered
- [ ] Confirm the two `open_in_browser` runs on Nov 2 showed the pages on screen. The tool committed, but nobody checked
- [ ] Install the boot service with `scripts/pi_setup.sh --service`, and add `--spotify-player` to `bot.env`
- [ ] Find why the bot is slow to exit: 62 s in run `20261102-095054-8e433b`, 2 s in others, 4 s on fakes. Each exit step now logs `shutdown_step`, MCP close has a 5 s limit, and on fakes MCP close took 0 ms, so read the steps after the next slow exit. The Nov 4 Mac runs were ended by closing the terminal tab, which cut the log short, so they give no reading. Nov 5: four Ctrl+C exits on the Mac, each under 2 s
- [x] Show on the face when TTS has failed: `tts_failed`, a sad face and "Voice is down" until audio plays again (fakes only, not yet on a real Cartesia failure)
- [ ] The cost ledger showed far less on Cartesia than the credits used, so check its price against the real balance
- [x] Keep the 50% mic gain: `wpctl` line in `scripts/pi_setup.sh`, not yet run on the Pi
- [x] `play` in the first 20 s after start: waits up to 4 s for the player, then says it is still starting and to try again (fakes only). The 10 s tool timeout rules out a full 20 s wait
- [ ] Decide what to do with VCM and raspotify on the Pi: VCM is stopped and disabled, raspotify still runs
- [x] Merge `build/pi-port` into main (Nov 3, local only)
- [ ] Enhancement, parked: music lyrics reach the transcript and can trigger tools. In `20261102-123315-a8d32d`, a lyric was followed by `stop_music`. Options: lower the music while speech is detected, or add the music to the echo canceller's reference through a PipeWire virtual sink

## Multi-stage jobs (ADR-028)

- [x] `write_file` and `edit_file` run at once and keep replaced versions in `.versions`
- [x] Anthropic `max_tokens` raised to 8,192 so a whole page fits in one call
- [x] System prompt: plan first, work to the end, check, then offer to open it
- [x] Due timers and alarms are said aloud (`harness/timer_watch.py`, fakes only)
- [x] Live command with the real shell and Tavily search (`docs/tools.md`)
- [x] Run a website job on Haiku in text: passed once, $0.0061, 19 s, and the page rendered (ADR-028)
- [x] Run a website job by voice with `--ui`: Flappy Bird and a UP AI page on the Pi, Nov 2. Their quality is not checked
- [x] Repeat the website job and try a larger site (Nov 3, text, Haiku): small site 2 of 2 in 14 to 28 s, a three-page cafe site 2 of 2 in about 80 s and $0.02, every page and `plan.md` written. Page quality not checked. Earlier runner results were void: it gave `write_file` an undo window, fixed
- [x] Try a job that uses `bash` and `serve` (Nov 3, text, live sandbox): 2 of 2, one read-back and one yes per command. The read-back speaks flags aloud ("python3 dash m http.server 8000"), which is clumsy by voice
- [x] Decide how a long silent job should sound: a rotating thinking phrase, on by default, reply templates off (Nov 4, ADR-033)
- [ ] Prune `.versions` if it grows (one 2.5 KB file on Nov 3, nothing to do yet)

## Check after the Nov 2 live run

- [ ] Run a live session on the fixes made after the last mic run. Seen live on Nov 4: tools called with no preamble, and an unclear fragment ("Only a certificate.") got a short question back. Still untried live: `restart_track`, and desktop commands refused in `bash`
- [ ] Run `open_in_browser` with `ME3_WORKSPACE_LIVE=1` and a spoken yes (ADR-026)
- [x] `tts_starved` at about 0.02x was a false reading: hop windows ran across the wait between replies. Fixed, and speech arrives at 2x to 11x real time. Most output gaps were pauses between sentences or during tools
- [ ] Two real speaker write stalls in `20261102-095054-8e433b` (1,889 ms and 313 ms) with audio buffered, cause unknown. `loop_lag` events now log a stalled event loop. In the 5 minute Mac run on Nov 4 the only one was 229 ms during shutdown, none while talking, so check again on the Pi, where the stalls happened
- [x] The `phantom_turn` in run `20261102-000823-8958d3` was a false alarm: the run stopped with that turn open. The report now skips such turns

## Finish the first spike

- [x] Step 4, Cartesia arm (ADR-006): ran Nov 3. It truncates. The reported 4 and 5 extra words (immediate, stop 279 ms; duck, stop 1,298 ms) came from the bench's 850 characters a minute guess: Cartesia speaks about 1,000 to 1,150, so the real extra is 1 to 2, as on `fake_words` (research.md, Nov 4). The bench now counts heard words from the word timestamps, and `fake_words_fast` covers it
- [x] One `--tts cartesia` run with the fixed bench confirmed it: over-commit 1 word, stop 245 ms (Nov 4, `20261104-201942-2a1054`, $0.0246)
- [ ] Step 7, Pi port (needs a go):
  - [x] Write `scripts/pi_setup.sh`, the `me3-bot` service, the kiosk launcher, and `docs/quickstart-pi.md` (ADR-030), not run on a Pi
  - [x] Run the setup and tests on the Pi: Bookworm, not Trixie, 450 passed, and the `say` failures fixed
  - [x] `bwrap` sandbox passes the doctor, and `xdg-open` opened a page by hand (ADR-024, ADR-026)
  - [ ] Live `bash` and `serve` inside `bwrap` on the Pi
  - [x] WebRTC AEC from `livekit` on the Pi: 0.34 ms per 20 ms block (ADR-029)
  - [x] Set the audio devices in `pi.yaml` to `pipewire` (ADR-029)
  - [ ] Echo test on the USB mic and speaker: 60 s of bot speech with nobody talking gives no `user_turn_start`, then a real barge-in still works. Two USB clocks drift, so run it long. Fall back to PipeWire echo-cancel (`scripts/pipewire_aec.conf`) if WebRTC AEC fails
  - [x] Spotify on the Pi: plays in the face with `--spotify-player` (ADR-031)
  - [x] CPU, temperature, memory, and throttling logging (`system_stats` events, doctor `pi` section), not run on a Pi
  - [x] Smart Turn timing on a Pi: 158 ms a decision, over the 120 ms budget, so stay on Flux
  - [ ] Voice server cost on the Pi: `bench/voice_hop_probe` (audio frames) and `bench/voice_bridge_probe.py` (MCP events)

- [x] Check Cartesia delivery from the Philippines: smooth by ear, and used for all live runs since (ADR-015)
- [x] Spoken fallback on LLM errors such as Groq rejecting `set_timer` with 0 seconds (`harness/recovery.py`, fake-tested)
- [x] Give the agent a real clock (`get_time`, daaf377)
- [ ] Live speaker test with `--echo-cancel`: one run passed with no self-echo and a real barge-in (ADR-015). Two more speaker runs on Nov 4 (`20261104-202417-64248a`, `20261104-204433-6a77ad`): one turn in the first was the bot hearing its own words and cutting itself off, the second had none. Still to check: the first-speech blip (ADR-013)

Set aside because Cartesia replaced Aura-2 as the live TTS (ADR-015), and worth doing only if Aura-2 comes back:

- [ ] Exercise the ADR-012 socket replacement against live Deepgram (fake-tested only)
- [ ] Choppy Aura-2 speech: delivery swings between 2.25x and 0.43x real time, not caused by sample rate (ADR-014)

## ADR-009 additions (approved)

Tool policy (Nov 1):

- [x] Spike wrapping `MCPClient` handlers (no documented hook in Pipecat 1.13) (ADR-011, 2b0d52b)
- [x] `harness/tool_policy.py` and `configs/tool_policy.yaml`: defaults from MCP hints, `permission`, `undo_s`, `timeout_s`, disabled tools removed from the schema (2b0d52b)
- [x] Spoken confirmation and 5 s undo, with "cancel" working in both barge-in modes (679207a; fake STT, not yet with Flux interim transcripts)
- [x] Startup check for duplicate tool names; log every policy decision (2b0d52b, 679207a)
- [x] Protect only the commit step, never block the mic (2b0d52b)
- [x] Check `waiting_for_undo` and `needs_confirmation` handling on a real LLM (Groq `gpt-oss-20b`, one run: 23 of 24, ADR-011)

- [x] Start the undo window when the announcement finishes, not when the tool is held (ADR-017, fake-tested)

Test layers (Nov 1 onward):

- [x] `bench/task_runner.py` and `bench/task_suite/cases/`: next reply, tool call, conversation (ab86a0b)
- [ ] Audio timing layer: extend `bench/interruption_test.py`
- [x] `stay_silent` cases (`addressing.yaml`)
- [x] `--repeat`, pass rates, fixed failure buckets, cost per successful task (ab86a0b)
- [x] Clock cases first, then one set per new server (ab86a0b, bc171bd)
- [x] Separate ledger price for `gpt-oss-120b` (from trackers; recheck on console.groq.com/pricing before the comparison)

Waiting phrase (Nov 2):

- [x] Phrase cache, shared with the network-loss phrases and the spending-cap notice (`harness/phrase_cache.py`)
- [x] Generate the real clips (Nov 4, `bench.make_phrases`)
- [x] Five rotating clips, 1500 ms, slow tools only on start, a prompt rule against saying one, on by default (Nov 4, ADR-033)
- [x] Check the 1500 ms threshold and the slow-tool list on a live run (Nov 4): 4 of 15 turns, every tool turn, none without a tool. See "Found in the Nov 4 live runs"
- [x] Triggers: tool call start, or no reply audio after `filler.after_ms`; off by default in `mac.yaml` and `pi.yaml` (e2f977b)
- [x] Once per turn, interruptible, kept out of the LLM context, never for `stay_silent` or in IDLE (e2f977b; the IDLE and `stay_silent` hooks wait for those features)
- [x] `filler_audio_out` event and a `latency_probe` stage; log how often it plays (e2f977b; reply audio after a clip is an estimate)

## Latency (target p50 under 1.0 s)

Three long mic runs on Nov 1 and 2, 59 replies, end of turn to first audio by framework events: p50 1.84 s, or 1.08 s when no tool ran first and 2.31 s when one did. Worst 5.7 s. These start at end of turn, so they leave out the end-of-turn wait. From Nov 4 the headline is end of speech to first reply audio from the samples (ADR-036); on fakes it is about 0.35 s, almost all end-of-turn wait.

- [x] Tune Flux end of turn: `eot_threshold` stays 0.7, timeout unchanged, eager stays off (ADR-018)
- [x] Spike `ForceEndTurn` with Smart Turn: built as `turn.stop: flux_forced`, failed the 300 ms rule on questions, stays off (ADR-019)
- [ ] Optional: try a shorter Smart Turn `stop_secs` with `flux_forced`, whose worst waits reach 5.7 s (ADR-019)
- [ ] Check mid-sentence pauses on the live mic: `say` pauses may be harsher than a person (ADR-018)
- [ ] Add Deepgram Nova-3 so Smart Turn can be A/B tested against Flux fairly
- [x] Build reply templates to skip the second LLM call: off by default, saved 296 ms against a 300 ms bar on 120b (ADR-020)
- [x] Measure from real audio, not framework events: `latency_probe` reads sample-level marks at the input and output, drops three warm-up turns per session, and reports p50/p90/p95 for tool and no-tool turns with cost per turn; `latency_bench` makes the runs on fakes (ADR-036, branch `claude/latency-bench-e97788`)
- [ ] Run `latency_bench` live for 30 or more kept turns per condition, with `ME3_LIVE=1` and approval, at two times of day
- [ ] Stop latency from the same audio marks: interrupter onset to output silence
- [ ] Cost per successful task: join the task judge to the probe's cost per turn
- [ ] Try Anthropic prompt caching: the tool schemas repeat on every call, so it may cut cost and first token (ADR-025)
- [ ] Shorten the gap after tool calls, the largest remaining cost on tool turns

## Barge-in

- [ ] Replace the 100 ms history-ordering workaround: let the aggregator own the turn start (`ExternalUserTurnStartStrategy`) (ADR-007)
- [ ] Get duck-mode stop under 300 ms (about 600 ms with Flux now): local stop-word spotter, or act on Flux partial transcripts sooner
- [ ] Choose the default between `immediate` and `duck`, after the Flux and Pi echo measurements
- [x] Test-runner fix: a backchannel run now waits 15 s, not 60 s
- [ ] Resume policy after an interruption: resume, summarize the unplayed part, or drop it (review improvement 4)

## Voice servers (ADR-032)

- [x] Real `voice-listen` (Flux) and `voice-speak` (Cartesia) as thin relays, tested on fakes over stdio and HTTP (Nov 4)
- [x] Audio side channel: a Unix socket per stream (Nov 4)
- [x] One live Mac run with `stt=voice_listen,tts=voice_speak`, and the Cartesia interruption bench through the servers (Nov 4)
- [x] Measure the extra audio hop through the servers on the Mac (`bench/voice_hop_probe`, Nov 4): under 0.5 ms a frame each way, +0.6 ms (stdio) and +2.1 ms (HTTP) from request to first chunk, +6 to 21 ms per stream open (ADR-032). After the event queue (a3bec1e): first-chunk p95 3.3 ms on stdio, was 17 ms
- [x] Send voice events without blocking the relay's down loop (Nov 4, a3bec1e): first-chunk p95 fell from 17 ms to 3.3 ms on stdio
- [ ] Switch the Pi service and the live command to the voice stages: the Mac live run passed on Nov 4, so this waits only on the Pi run
- [x] `append_text`, `pause`, `resume`, `interrupt(kind)` dropped: Pipecat sends these as Cartesia messages on the stream (ADR-032)
- [x] Wire `VoiceEventFrame`s into pipeline actions (ADR-008): dropped. With thin servers, Pipecat's own Flux and Cartesia services act on the provider messages, and the voice events are logged for the benchmark (ADR-032)
- [x] Reconnect when a voice server's MCP session or stream drops after a long idle (Nov 4, fakes): provider drop, a refused reconnect, a dead server process, Cartesia's idle timeout, and a stop during a connect all recover, over stdio and HTTP, with no leaked socket, call, or process. A whole bot run heard and spoke a second turn after both streams dropped (ADR-032, `tests/test_voice_reconnect.py`)
- [x] Due timers announce themselves (done in the bot instead, `harness/timer_watch.py`)

## Task servers

- [x] `music` mock
- [x] `music` real backend (Spotify, demo only, ADR-022)
- [x] `lights` mock
- [ ] `lights` real backend (check whether the Xiaomi bulb can be controlled locally)
- [x] `notes` (SQLite) (bc171bd)
- [x] `thermostat` mock (bc171bd)
- [x] `comms` mock (bc171bd)
- [x] `knowledge`, `docs`, `workspace` on fakes and mocks (ADR-021)
- [x] Weather and fetch real by default, Tavily search per run, all used on the live mic (ADR-024)
- [x] File tools, and `bash` and `serve` behind the guard and sandbox, on the Mac (ADR-024)
- [x] `open_in_browser` for workspace HTML (ADR-026, live run still to do, see above)
- [x] `comms` phone bridge built, dry run only (ADR-024, real send waits on approval)
- [x] Read-back from real arguments for open world destructive tools (ADR-024)
- [x] `delegate` on fakes: own worker loop on Haiku 5.5, only what needs no yes, step and cost caps, announced at a quiet moment (ADR-035)
- [x] One live `delegate` task on Haiku 5.5: wrote two files and listed them, 3 model calls, 4.6 s, $0.00095 (Nov 4, from a script)
- [x] A live `delegate` task by voice: wrote `packing.txt` and was announced about 8 s later (Nov 4, `20261104-202417-64248a`)
- [ ] A live `delegate` task that stops on "needs your yes"

## No-wake-word addressing

- [x] Loudness gate with 400 ms of pre-roll, off by default, synthetic audio only (ADR-016)
- [ ] Check Flux with gaps in the audio before enabling the gate with it (ADR-016)
- [x] Run `bench/task_suite/cases/addressing.yaml` on Groq (ADR-017)
- [x] Relax `addr_short_follow_up_in_active`: accepts "thanks" or "welcome" (ADR-017)
- [ ] Fix side talk on Haiku before enabling addressing: it answered both `addr_side_talk` cases (ADR-025)
- [ ] Tune the IDLE word rules on a recorded room
- [x] ACTIVE/IDLE conversation states (ADR-016)
- [x] `stay_silent` tool (ADR-016)
- [x] `require_wake_after_idle` fallback switch (ADR-016)
- [x] Mute switch in software (`kill -USR1`, log line); a hardware button and light wait for the Pi
- [x] Privacy measures listed in `docs/privacy.md` (Nov 3)

## Model comparison and benchmark

- [x] Repeat the Haiku task suite (Nov 3): 321 of 340 over 5 repeats, $0.52. Failures: side talk 0 of 10 (known), `notes_delete_runs_after_undo` 2 of 5 (lists notes, never deletes), `notes_conv_undo_keeps_note` 4 of 5, and the site case, void from the runner bug and 2 of 2 after the fix
- [ ] 40 tool-calling turns plus 20 non-addressed utterances, 5 repeats each, against the decision rule, through `bench/task_runner.py`. Haiku is the baseline, other candidates after purchases are approved
- [ ] Full-Duplex-Bench adapter (v1/v1.5, then v3)
- [ ] Our own task suite and team-recorded clips (including self-corrections)
- [ ] Acoustic stop-latency measurement
- [ ] Cost per successful task in every report

## Reliability and operations

- [x] Network-loss detection: a TCP probe tells an outage from a bad request, `network_lost` plays once, the face shows it, recovery is logged (Nov 4, ADR-037, fakes only)
- [ ] Try a real outage once: turn off Wi-Fi mid-session and check the clip, the face, and recovery
- [x] Make the `network_lost` and `cap_reached` clips (Nov 4, $0.0051)
- [ ] Provider failover per stage, reconnect with backoff, health log
- [ ] systemd services with restart on failure, auto-start on boot, a watchdog
- [x] Notice when the daily cap is hit, now the stored `cap_reached` clip, so it plays even when TTS is the capped provider (ADR-037)
- [x] Alert at $10 total spend (doctor warning)
- [x] Doctor and run report (`bench/doctor.py`, `bench/run_report.py`)

## Housekeeping

- [ ] Recheck Pipecat deprecations on every upgrade (ADR-003)
- [ ] Recheck prices on the day each provider goes live (several are promotional)
- [ ] Not yet researched: most providers' terms on data use
- [x] Delete `music-spotify`: compared Nov 3, its work is already on main

## Done

- [x] Repo, pinned versions, CLAUDE.md, configs (ADR-001, ADR-002)
- [x] Cost ledger with hard daily caps; TTS metered from text (Aura-2 reports no usage)
- [x] Clock MCP server with annotated tools
- [x] Gate 1, scripted and live, over stdio and HTTP (ADR-004, ADR-005)
- [x] Step 4 barge-in truncation test: fake, fake_words, Aura-2 (ADR-006)
- [x] Step 5 duck-then-decide prototype (ADR-007)
- [x] Step 6 VoiceMCPBridge with stub voice servers (ADR-008)
