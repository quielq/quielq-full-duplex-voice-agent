# ME3 to-do list

Open work only, grouped by area. Closed items and their history are in git (`git log -p TODO.md`) and in the ADRs. Tick an item when it closes and note the ADR or commit that settled it, or delete it in the next cleanup. Last cleaned up: Nov 25, 2026.

Debugging: start with `uv run python -m bench.doctor`, then `uv run python -m bench.run_report --live`. Components that are built but switched off are listed in `docs/switches.md`.

## Waiting on Quiel

- [ ] Approve a first real message and call through the phone bridge (ADR-024)
- [ ] Later purchase: OpenRouter ($5), only if the model comparison goes ahead
- [ ] One live mic run saying the owner's name a few times, then replace the guessed mishearings in `configs/owner.yaml` with what Flux wrote (ADR-050)
- [ ] Delete the `via-test` folder from the Pages repo, the Nov 16 test publish (ADR-049)

## From the software-building work (Nov 16 to 25)

- [ ] Measure first token time with prompt v4 against v3 on a short live run: the prompt grew from about 330 to about 870 words (ADR-050)
- [ ] In the live build run Haiku skipped `notes.md` and still said a short sentence before some tool calls. Try again by voice, and tighten the `<building>` section if it repeats
- [ ] A spoken run of build, publish, and open, with both read-backs and undo windows (ADR-049). Only the server functions have run live
- [ ] Check that the Flux keyterm add-on shows on the Deepgram bill at the ledger's price, $0.0013 a minute (ADR-050)
- [ ] See the question card and project card on the Pi's 7 inch screen (ADR-051)
- [ ] Optional: a QR code for the published link on the face, so others can open it on a phone (ADR-051)
- [ ] A scripted text run ends at the first reply with words, so a long build needs a pause after the request. Consider a `--wait` flag for `harness.bot --input text`

## Pi port (ADR-029, ADR-030, ADR-031)

- [ ] Step 7, Pi port (needs a go):
  - [ ] Live `bash` and `serve` inside `bwrap` on the Pi: `bash` passed Nov 5 (read-back, spoken yes, 5 s undo, `mkdir` ran, `20261105-203514-1a2f7a`). `serve` not tried
- [ ] Live speaker test with `--echo-cancel`: one run passed with no self-echo and a real barge-in (ADR-015). Two more speaker runs on Nov 4 (`20261104-202417-64248a`, `20261104-204433-6a77ad`): one turn in the first was the bot hearing its own words and cutting itself off, the second had none. Still to check: the first-speech blip (ADR-013)
- [ ] Exercise the ADR-012 socket replacement against live Deepgram (fake-tested only)
- [ ] Choppy Aura-2 speech: delivery swings between 2.25x and 0.43x real time, not caused by sample rate (ADR-014)
- [ ] IPv6 on the Pi's Wi-Fi (Nov 6): the network started handing out IPv6, the path did not reach Cartesia, and voice-speak's WebSocket tried it first and timed out on every reply. Worked around with `sysctl net.ipv6.conf.wlan0.disable_ipv6=1`, which a reboot undoes. A lasting fix would make voice-speak connect over IPv4 only, or prefer IPv4 in `/etc/gai.conf`
- [ ] Firmware stall, Nov 5, before the run: a firmware call timed out, the touch controller timed out 9 times, the USB mic dropped ("Not enough bandwidth", -62), and the Pi rebooted. Power read fine. Cause unknown; if it happens again, save `dmesg` before rebooting
- [ ] Confirm the two `open_in_browser` runs on Nov 2 showed the pages on screen. The tool committed, but nobody checked
- [ ] Install the boot service with `scripts/pi_setup.sh --service`, and add `--spotify-player` to `bot.env`
- [ ] `shutdown_step` for `pipeline` counts the whole run: `last_step` in `harness/bot.py` is set at start, not when the exit begins, so a slow pipeline stop cannot be told apart from a long run. Reset it when the exit starts
- [ ] The cost ledger showed far less on Cartesia than the credits used, so check its price against the real balance
- [ ] Decide what to do with VCM and raspotify on the Pi: VCM is stopped and disabled, raspotify still runs. Nov 5: the `vcm`, `vcm-home`, and `raspotify` user units were inactive, and none held the mic; a system level raspotify unit was not checked
- [ ] Enhancement, parked: music lyrics reach the transcript and can trigger tools. In `20261102-123315-a8d32d`, a lyric was followed by `stop_music`. Options: lower the music while speech is detected, or add the music to the echo canceller's reference through a PipeWire virtual sink

## Found in live runs (Nov 2 to 5)

- [ ] Run a live session on the fixes made after the last mic run. Seen live on Nov 4: tools called with no preamble, and an unclear fragment ("Only a certificate.") got a short question back. Still untried live: `restart_track`, and desktop commands refused in `bash`
- [ ] Run `open_in_browser` with `ME3_WORKSPACE_LIVE=1` and a spoken yes (ADR-026)
- [ ] Cost: the 18-turn run was $0.21, about $0.012 a turn, mostly Cartesia. Each Haiku call sends about 8,400 prompt tokens. Prompt caching is now on (ADR-045): recheck the cost per turn
- [ ] The end-of-turn wait is the largest part of the headline at 1.3 s p50 (ADR-036)
- [ ] "wait" was heard as "Great." The bot stopped, then answered it as a new turn ("Glad that helped"). Heard right once on the Pi (`20261105-203514-1a2f7a`), so keep watching
- [ ] A short "Okay." over the bot's reply cuts it off, and it restarts ("Sorry, my last reply cut off"). Belongs with backchannel handling in Barge-in
- [ ] Misheard names: "UP Diliman" as "Yuki Deleman", "Mayon Builders" as "my on builders". On the second, DeepSeek answered without a docs tool call
- [ ] The face server printed an aiohttp "Missing return statement on request handler" traceback at exit
- [ ] Weather turns took 4.5 to 6.1 s to first audio, the slowest kind of turn in these runs (4.8 to 5.6 s again in `20261105-202141-d17a53`, and 5.1 and 5.7 s on the Pi in `20261105-203514-1a2f7a`)
- [ ] Haiku spoke a remark about the misheard place before the weather call ("The place name is likely Cadon City or Caddon City; I'll use it as given."). The preamble list does not cover it, and the prompt already says not to remark on mishearing
- [ ] Self-echo got through: the bot's own "three text files" came back as "Three chest pains", cut it off, and got emergency advice. Silero gave no onset, so Flux alone started the turn from echo left after AEC, and the ADR-042 guard missed it because the garbled words do not match. Options: require a local VAD onset before a Flux turn start while the bot speaks, or match on sound alike words
- [ ] `get_weather` on a made up place ("Flats") found some real place and returned only its name, so the bot could not say where it was and three turns went in circles. Return the region and country from the geocoder and have the bot say them
- [ ] Haiku spoke "Let me check the thermostat." before a tool call, after the light answer in the same reply. The ADR-041 drop covers only a reply that opens with the filler
- [ ] "Grindelwald" was transcribed once as "Brindlewood", and the bot kept offering to look up Brindlewood
- [ ] The user singing at the start was answered as a turn ("Since")

## Robot face (ADR-027, ADR-038)

- [ ] Decide what `open_in_browser` does on the Pi: it covers the face, and touch has no way back
- [ ] When the bot stops, the kiosk leaves Chromium on an error page: `scripts/pi_kiosk.sh` only waits for the face before the first launch. Reload when the face comes back (Nov 5)

## Multi-stage jobs (ADR-028)

- [ ] Prune `.versions` if it grows (one 2.5 KB file on Nov 3, nothing to do yet)

## ADR-009 additions (approved)

- [ ] Audio timing layer: extend `bench/interruption_test.py`

## Latency (target p50 under 1.0 s)

- [ ] Optional: try a shorter Smart Turn `stop_secs` with `flux_forced`, whose worst waits reach 5.7 s (ADR-019)
- [ ] Check mid-sentence pauses on the live mic: `say` pauses may be harsher than a person (ADR-018)
- [ ] Add Deepgram Nova-3 so Smart Turn can be A/B tested against Flux fairly
- [ ] Run `latency_bench` live for 30 or more kept turns per condition, with `ME3_LIVE=1` and approval, at two times of day. Nov 5, 22:33 to 22:46, Pi, voice servers, Haiku: 35 kept turns over five sessions, p50 2.59 s, p90 3.09 s (no tool 2.47 s over 18, tool 2.72 s over 17), $0.26 (ADR-036). Left: a second time of day, and 30 per group if "per condition" means tool and no-tool
- [ ] Stop latency from the same audio marks: interrupter onset to output silence
- [ ] Cost per successful task: join the task judge to the probe's cost per turn
- [ ] `bench.latency_bench` refuses on the Pi even with every clip cached: `request_wav` checks for macOS `say` before it checks the cache. Check the cache first. Nov 5 workaround: copy `data/latency_bench/wavs` from the Mac and put a stand-in `say` that exits 1 first on `PATH`
- [ ] Find out why Haiku hands `workspace_plan_and_build_a_larger_site` to `start_task`: a few repeats with the old and new prompts (ADR-046)
- [ ] Shorten the gap after tool calls, the largest remaining cost on tool turns

## Barge-in

- [ ] Replace the 100 ms history-ordering workaround: let the aggregator own the turn start (`ExternalUserTurnStartStrategy`) (ADR-007)
- [ ] Get duck-mode stop under 300 ms (about 600 ms with Flux now): local stop-word spotter, or act on Flux partial transcripts sooner
- [ ] Choose the default between `immediate` and `duck`, after the Flux and Pi echo measurements. On the class benchmark, duck stopped about 0.2 s later than immediate (ADR-045)
- [ ] Resume policy after an interruption: resume, summarize the unplayed part, or drop it (review improvement 4)

## Voice servers (ADR-032)

- [ ] Switch the Pi service and the live command to the voice stages: the Pi run passed on Nov 5 (`20261105-203514-1a2f7a`, upstream connect about 0.7 s each, relay hops 3 to 12 ms), so only the service change is left

## Task servers

- [ ] `lights` real backend (check whether the Xiaomi bulb can be controlled locally)
- [ ] A live `delegate` task that stops on "needs your yes"

## No-wake-word addressing

- [ ] Check Flux with gaps in the audio before enabling the gate with it (ADR-016)
- [ ] Fix side talk on Haiku before enabling addressing: it answered both `addr_side_talk` cases (ADR-025)
- [ ] Tune the IDLE word rules on a recorded room

## Model comparison and benchmark

- [ ] 40 tool-calling turns plus 20 non-addressed utterances, 5 repeats each, against the decision rule, through `bench/task_runner.py`. Haiku is the baseline, other candidates after purchases are approved
- [ ] Our own task suite and team-recorded clips (including self-corrections)
- [ ] Acoustic stop-latency measurement
- [ ] Cost per successful task in every report

## Reliability and operations

- [ ] Try a real outage once: turn off Wi-Fi mid-session and check the clip, the face, and recovery
- [ ] Provider failover per stage, reconnect with backoff, health log
- [ ] systemd services with restart on failure, auto-start on boot, a watchdog

## Housekeeping

- [ ] Recheck Pipecat deprecations on every upgrade (ADR-003)
- [ ] FastMCP warns that the MCP logging capability is deprecated as of 2026-07-28 (SEP-2577), seen in `bench.voice_bridge_probe` on the Pi, Nov 5. Voice events ride it (ADR-008), so check what replaces it before the next `mcp` or `fastmcp` upgrade
- [ ] Recheck prices on the day each provider goes live (several are promotional)
- [ ] Not yet researched: most providers' terms on data use
