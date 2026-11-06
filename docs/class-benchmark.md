# Class benchmark results

The **class-benchmark** is the AI231 course benchmark, fd-benchmark ("fd" is full-duplex):
https://github.com/trueshotjames/ai231-me3-full-duplex-benchmarking. It adapts Full-Duplex-Bench v1 (Lin et al., 2025)
and v1.5 (Lin et al., 2026) and adds the course's missed and false turn-taking rates. It plays the papers' test clips
into the agent, records what she says back, and scores it with the papers' rules. Its method is in its
`docs/METHOD.md`.

Everything else we measured is the **self-benchmark**: our own latency bench and probe (ADR-036), the interruption
bench (steps 4 and 5), the task suite, and the end-of-turn sweep (ADR-018). Those numbers are not comparable to the
ones here, because the class benchmark times from the clip's annotated turn end, not from our own audio marks.

## How we ran it

- fd-benchmark at commit `293ba44` (Nov 5, 2026), cloned into `fd-benchmark/` and used unchanged. Its datasets came
  from its GitHub release (`tools/fetch_datasets.py`).
- Direct feed: the clips reach the agent over a local WebSocket, with no microphone or speaker. The launcher builds
  the transport and calls our entry, `bench/fdb_adapter.py`, through `FDBENCH_ENTRY` (ADR-044). The bot's
  `fdbench` input mode runs the same VAD, STT, end of turn, LLM, TTS, tool policy and cost ledger as the mic. With Flux as
  the STT, the end of turn is Flux's own model (`turn.stop: auto`), not Smart Turn.
- Two settings differ from a mic run: echo cancelling is off (the clips arrive as samples, so there is no echo), and
  each session's tools start from an empty data folder (so a timer from an earlier run cannot ring during a clip).
- Stages: Flux and Cartesia through our voice servers, and Claude Haiku 5.5 (`claude-haiku-5-5`), with the Mac
  profile. Addressing (ADR-016) and duck mode are off, as they are by default, so barge-in is immediate.
- The LLM judge is GPT-4o through OpenRouter, the model the papers used, added after each run with
  `bench/fdb_judge.py`, which records its spend in our cost ledger ($0.07 for both Nov 6 runs). It gives the
  interruption relevance score and the v1.5 Respond / Resume labels. ASR for word counts is faster-whisper small.
  UTMOSv2 is not installed.

```
cd fd-benchmark
FDBENCH_ENTRY=$PWD/../bench/fdb_adapter.py ME3_PROFILE=mac ME3_STAGES=stt=voice_listen,llm=anthropic,tts=voice_speak \
  .venv/bin/python -m fdbench run --student quielq --rounds 5 --connect direct --where local \
  --judge-provider none --yes --recordings k --no-live
cd ..
uv run python -m bench.fdb_judge fd-benchmark/runs/quielq/<run folder>
```

`FDBENCH_ENTRY` must be an absolute path, or the launcher falls back to the course layout and stops.

## What the metrics mean

These definitions follow fd-benchmark's `docs/METHOD.md`, which takes the rules from the papers' released code.

### Trials and the basic rule

- **Trial**: one test clip played into the agent while her reply is recorded. Between trials she gets 6 s of silence.
  A **round** plays one clip from every group, so five rounds use five different clips per group.
- **Taking the turn**: within the window the metric scores, she spoke for at least 1 s, or for less but with more
  than 3 words (words come from a faster-whisper transcript of her audio). Everything below that says "took the turn"
  uses this rule.
- **TOR (takeover rate)**: the share of trials in which she took the turn. Whether high or low is good depends on the
  test: she should take the turn when the user is done, and not when the user is only pausing.
- **Brackets** after a rate are its 95% interval (Wilson). With 5 trials a perfect 5/5 still means anything from
  56% to 100%.
- **Latencies** are measured on the recorded audio with Silero VAD, the speech detector the papers use. fd-benchmark
  shows the median, mean and p95; the papers report means.

### Full-Duplex-Bench v1 tests

- **Pause handling** (synthetic and Candor clips): the user pauses mid-sentence and then goes on. TOR is the share of
  clips where she cut in during the clip. **Lower is better.** Synthetic clips have a made-up pause; Candor clips are
  real conversation.
- **Turn-taking** (Candor clips): the user finishes a turn that she should answer. TOR is the share she answered within
  8 s of the annotated end of turn. **Higher is better.**
- **Turn-taking latency**: from the annotated end of the user's turn to the first audio of her answer. **Lower is
  better**; fd-benchmark's README marks under 1 s as the goal.
- **Interruption**: she is asked a question, and 1.5 s after she starts answering the user cuts in with a new
  request. TOR is the share where she took the turn after the cut-in (that is, she stopped and answered it). **Higher is
  better.**
- **Interruption latency**: from the end of the cut-in to the first audio of her reply to it. **Lower is better.**
- **Judge score, interruption**: an LLM rates from 0 to 5 how well her reply addresses the cut-in, using the authors'
  prompt (GPT-4o here, as in the paper).

### The course's two rates

- **Missed turn-taking rate**: 1 minus the turn-taking TOR, that is, the share of turns meant for her that she did not
  answer within 8 s. **Lower is better.**
- **False turn-taking rate**: clips that are not addressed to her, played while she is idle: side talk (people talking
  to each other), TV or radio, background speech, and noise. The rate is the share she answered, by the same rule, from
  the start of the clip to 8 s after it ends. **Lower is better.** No published system has a number for this yet.

### Full-Duplex-Bench v1.5 tests

Each trial plays a user turn; 1.5 s after she starts answering, an overlap is played over her. Then the same turn is
played again without the overlap (the **clean twin**), as a reference.

| Scenario | What overlaps her | What she should do |
|---|---|---|
| Interruption | the user cuts in with something new | **Respond**: stop and address it |
| Backchannel | the user says "uh-huh", "sure yeah" | **Resume**: keep going with her answer |
| Talking to others | the user turns to speak to someone else | **Resume** |
| Background speech | someone else talks in the room | **Resume** |

- **Respond / Resume / Uncertain / Unknown**: an LLM judge reads transcripts of the overlapped and clean trials and
  labels what she did. Respond means she addressed the overlap; Resume means she carried on with her original answer,
  whether she paused first or talked straight through. Uncertain means she handled it unclearly, and Unknown means it
  cannot be told (for example, she never answered the first turn, so there was nothing to overlap). The labels are
  about content, not timing.
- **Timing shares** (shown beside the labels, from the audio alone): **kept talking** (she spoke straight through the overlap),
  **paused and came back** (she stopped and later spoke again, which can be Respond or Resume), and **stopped, silent**
  (she stopped and said nothing more).
- **Stop latency (t_stop)**: from the start of the overlap to the end of the speech she was producing. **Lower means
  she yields faster**, which is right for an interruption but not needed for a backchannel.
- **Response latency (t_resp)**: from the end of the overlap to her next speech. **Lower is better.**
- **Voice change**: how her voice differs after the overlap compared with before it, on the interruption clips. Words
  per minute, pitch mean and spread, and loudness mean and spread, each as after minus before, with a paired t-test
  (p under 0.05 counts as a real change). The paper expects a natural speaker to speed up, raise and vary pitch, and
  get slightly quieter after being interrupted. UTMOSv2, a speech quality score, is left out because it is not
  installed.

## Mac, Nov 6, 2026: five rounds

fdbench run `2026-11-06_10-28-03`, ME3 run `20261106-102804-7e95d7`, MacBook Air with 8 GB. Five rounds draw five
different clips per group (50 clips, 75 trials with the v1.5 clean twins). It took 26 minutes and cost $1.08, about
$0.22 a round (Cartesia about 88% of it).

The benchmark sets no number of rounds; each group has 30 clips. Five clips per group still gives wide intervals
(shown in brackets, 95%), so read small differences with care.

An earlier one-round run the same morning (`2026-11-06_09-35-44`, `20261106-093545-e87fc7`, $0.23) was the setup
check. It agreed with these results except that it cut into its one Candor pause.

### Full-Duplex-Bench v1 and the course rates

| Metric | Result | Better |
|---|---|---|
| TOR, pause handling (synthetic) | 0% (0/5) [0 to 43] | lower |
| TOR, pause handling (Candor) | 20% (1/5) [3 to 62] | lower |
| TOR, turn-taking (Candor) | 100% (5/5) [56 to 100] | higher |
| Missed turn-taking rate | 0% (0/5) [0 to 43] | lower |
| False turn-taking rate | 80% (8/10) [49 to 94] | lower |
| of which side talk | 100% (3/3) | lower |
| of which TV or radio | 100% (3/3) | lower |
| of which background speech | 100% (2/2) | lower |
| of which noise | 0% (0/2) | lower |
| Latency, turn-taking | median 2.51 s, mean 2.35 s, p95 2.88 s (n=5) | lower, under 1 s |
| TOR, interruption | 100% (5/5) [56 to 100] | higher |
| Latency, interruption | median 1.92 s, mean 1.92 s, p95 2.21 s (n=5) | lower |
| Judge score, interruption (0 to 5) | 4.8 mean (n=5) | higher |

Two pause trials that pass are marked provisional by the benchmark: she spoke for under a second at 0.1 s into the
clip, before any pause, which looks like the end of the previous trial's reply rather than a cut-in.

### Full-Duplex-Bench v1.5, timing only

| Scenario (expected) | Kept talking | Paused and came back | Stop latency, median | Response latency, median |
|---|---|---|---|---|
| Interruption (Respond) | 0/5 | 5/5 | 0.62 s (n=4) | 2.13 s |
| Backchannel (Resume) | 2/5 | 3/5 | 0.48 s (n=1) | 2.24 s (n=3) |
| Talking to others (Resume) | 0/5 | 5/5 | 0.90 s | 1.93 s |
| Background speech (Resume) | 0/5 | 5/5 | 0.60 s (n=4) | 2.66 s |

In 4 of the 20 overlap trials she had already finished her reply when the overlap came, so there was nothing to stop.
All 20 clean twins (the same turns without the overlap) were answered, after 1.32 to 2.99 s.

"Paused and came back" covers both of the paper's labels: Respond (she answered the new speech) and Resume (she went
back to her reply). The judge tells them apart from the transcripts (n=5 per scenario):

| Scenario (expected) | Respond | Resume | Uncertain | Unknown |
|---|---|---|---|---|
| Interruption (Respond) | **0.80** | 0.00 | 0.20 | 0.00 |
| Backchannel (Resume) | 0.00 | **0.40** | 0.40 | 0.20 |
| Talking to others (Resume) | 0.80 | **0.00** | 0.20 | 0.00 |
| Background speech (Resume) | 0.80 | **0.20** | 0.00 | 0.00 |

She handles a real interruption well (Respond 0.80, judge score 4.8 of 5), but she also answers speech that is not
meant for her: side talk and background speech get a reply 4 times in 5.

Voice change after an interruption (n=4, paired t-test): words per minute -58.9 (p=0.03), pitch mean -17.8 (p=0.17),
pitch SD -12.0 (p=0.17), intensity mean -0.7 (p=0.41), intensity SD +0.4 (p=0.45). She speaks slower after being
interrupted, the opposite of the speed-up the paper expects from a natural speaker; the other changes are not
significant at this size. Her voice comes from Cartesia, so this most likely reflects the TTS rather than any choice
the agent makes (not checked). As in the paper, only the interruption clips the judge labelled Respond count (4 of 5).

### What the five rounds show

- **She takes every turn meant for her and almost never cuts in.** 5/5 turn-taking, 5/5 interruptions, no missed
  turns, and 1 cut-in over 10 pauses. Flux's end-of-turn model waits out a mid-sentence pause.
- **She answers almost anything that sounds like speech.** 8 of 10 not-addressed clips got a reply (all side talk,
  TV or radio, and background speech); only the two noise clips were ignored. With addressing off she replies to any
  transcript. This is the clearest weakness.
- **She stops for every overlap.** Barge-in is immediate, so a backchannel, side talk or background speech stops
  her in 0.5 to 0.9 s, where the papers want her to carry on.
- **She is slow to answer.** Around 2 s from the end of the user's turn to her first audio, against the papers' 1 s
  mark. This is Flux end of turn, Haiku, and the first Cartesia audio, over the network.

## Mac, Nov 6, 2026: A/B runs

Three changes were tried on the same 30 clips as rounds 1 to 3 above (seed 174374, 3 rounds each), switched on through
the entry's environment (ADR-045). One trial out of 3 is within run-to-run noise.

| Change | Spend | Verdicts changed | Reply latency p50 | Stop on interruption, mean |
|---|---|---|---|---|
| None (rounds 1 to 3 above) | | | 2.35 s | 0.67 s |
| Prompt caching | $0.60 | +1 backchannel, -1 noise | 2.40 s | 0.64 s |
| Addressing (ADR-016) | $0.64 | +1 backchannel, -1 Candor pause | 2.38 s | 0.67 s |
| Duck-then-decide barge-in | $0.68 | -1 backchannel clean twin | 2.37 s | 0.86 s |

- **No change improved the scores or the latency.** The time goes to Flux's end-of-turn wait (0.8 s), Haiku's first
  token (1.0 s) and Cartesia's first audio (0.3 s), and none of these changes touches them.
- **Prompt caching cut the LLM cost by about 87%** and is now on by default.
- **Addressing could not act here**: she stays ACTIVE for 30 s after she speaks, and the trials come closer than that,
  so false turn-taking stayed at 0 of 5 correct.
- **Duck mode made stopping slower** by about 0.2 s, since the benchmark counts ducked speech as still talking.
- Flux eager end of turn was not run, to keep Cartesia credits for the demo.

## Pi, Nov 6, 2026: stage test and one round

The course run: ME3 on the Raspberry Pi 5 as the remote assistant (the guide's steps 8 to 11), fdbench on the Mac.
fd-benchmark at 57eaddb, which adds the course's stage-latency test and deck summary (`docs/STAGES.md` there) and the
class's targets. fdbench run `2026-11-06_21-22-21`, ME3 run `20261106-212224-666651` on the Pi.

- Stages: Flux and Cartesia through our voice servers, Claude Haiku 5.5, the Pi profile, prompt VIA v1 and the 20-turn
  context limit (ADR-047). Eager end of turn on at 0.5 (ADR-046). Barge-in immediate, addressing off.
- 10 phrases x 2 runs for the stage test (20 turns; `--runs 2` to save Cartesia credits), then one round of every group
  (10 trials). The stage marks come from fdbench's Pipecat observer, which the launcher attaches to our pipeline
  with no code change. Our `describe()` names the models on the results card.
- Spend: Cartesia 5,728 characters (ledger $0.29), Deepgram $0.09, Anthropic $0.01, judge $0.0068.

```
# Pi
cd ~/quielq-full-duplex-voice-agent/fd-benchmark
FDBENCH_ENTRY=$HOME/quielq-full-duplex-voice-agent/bench/fdb_adapter.py ME3_PROFILE=pi \
  ME3_STAGES=stt=voice_listen,llm=anthropic,tts=voice_speak ../.venv/bin/python launch_assistant.py \
  --stats-procs stt_mcp=servers.voice_listen,tts_mcp=servers.voice_speak,tools_mcp=servers.tasks
# Mac
cd fd-benchmark
.venv/bin/python -m fdbench run --student quielq --rounds 1 --runs 2 --connect direct --where remote \
  --judge-provider none --yes --recordings k --cooling "active cooler, fan"
cd ..
uv run python -m bench.fdb_judge fd-benchmark/runs/quielq/<run folder>
```

### Course metrics (template section 5)

Times on the Pi's monotonic clock. E2E is fdbench's definition: from the VAD end mark (Silero stops) to the first
reply audio handed to the output (`BotStartedSpeakingFrame`).

| Metric | Definition | Target | Mean | p95 | Median | n |
|---|---|---|---|---|---|---|
| E2E latency | end of speech to audio out | < 4.0 s | 1.46 s | 1.96 s | 1.29 s | 20 |
| ASR final | end of speech to transcript | < 0.8 s | 0.43 s | 1.18 s | 0.21 s | 20 |
| LLM TTFT | transcript to first token | < 1.5 s | 0.53 s | 0.70 s | 0.60 s | 19 |
| TTS TTFB | text to first audio chunk | < 0.6 s | 0.14 s | 0.18 s | 0.14 s | 20 |
| Barge-in | speech to playback stopped | < 0.8 s | 0.70 s | 0.79 s | | 2 |
| WER | Flux transcript against the clip's text | < 15 % | 5.5 % | | | 20 |

- Every row meets the class target, except ASR final's p95, which is over 0.8 s while its mean is under. ASR final
  here is when Flux confirms the end of the turn, so the slow turns are ones where Flux waited.
- **E2E heard**, from the clip's true end of speech to the start of her reply in the recording (fdbench's own clock,
  closest to our ADR-036 headline): median 1.57 s, mean 1.71 s, max 3.56 s. The VAD end mark came 0.11 s after the
  true end of speech (median). Our own Pi headline on Nov 5, before eager end of turn and the short-reply prompt and on
  synthetic `say` speech, was p50 2.59 s, so the two are not like for like.
- **Barge-in** has only two trials: 0.80 s on the v1 interruption clip and 0.60 s on the v1.5 one. With one round, the
  p95 is the slower of the two.

### Context size (template section 3)

| Context size | Median | Max | First to last call |
|---|---|---|---|
| Prompt tokens per LLM call (reported usage, cached part included) | 6,766 | 8,467 | 6,612 to 1,754 |
| Messages in context | 44 | 84 | 1 to 84 |

The whole run was one session of 41 user turns, so the 20-turn limit (ADR-047) trimmed the context 26 times. This is
its first live run.

### Results card (deck 2)

- Raspberry Pi 5 Model B Rev 1.1, 8 GB, active cooler with fan: peak 62.8 C, never throttled, ARM clock never
  below 1,500 MHz.
- CPU over all cores: median 4%, p95 7%; busiest core p95 18%. Lowest free RAM 6,522 MB. Per process: STT server 3.8%
  CPU and 329 MB peak, TTS server 0.1% and 94 MB, task servers 920 MB peak together.
- Network: Wi-Fi to the cloud, Wi-Fi to the Mac at 6 ms round trip. IPv6 turned off on the Pi's Wi-Fi for this run
  (below).
- Models: Deepgram `flux-general-en` through voice-listen, `claude-haiku-5-5`, Cartesia `sonic-3.6` through
  voice-speak, Silero VAD (Pipecat 1.13), prompt VIA v1.

The latency chart is `latency_chart.svg` in the run folder.

### Full-Duplex-Bench, one round

One trial per group, so these are a check that the Pi behaves like the Mac, not results of their own.

- v1: stayed silent through both pauses, took the turn after 1.61 s, took the interruption after 1.83 s with a judge
  score of 5. Both false turn-taking clips (side talk, TV or radio) got a reply, as on the Mac.
- v1.5: paused and came back on interruption, talking to others and background speech (judged Respond each time), and
  talked through the backchannel (judged Resume). Stop latency 0.60 s (interruption), 0.74 s (talking to others),
  0.22 s (background); response latency 1.57 to 1.89 s.

### A failed first try: IPv6

The first Pi run (`2026-11-06_21-13-33`, stopped after three trials, about 120 Cartesia characters) got no speech:
every Cartesia connection from voice-speak ended with `TimeoutError: timed out during opening handshake`. The Pi had
received an IPv6 address and default route from the Wi-Fi at 20:27 that evening. Cartesia publishes IPv6 addresses,
and the Pi tried them first, but the IPv6 path did not get through: a keyless handshake took 0.39 s over IPv4 and timed
out after 10 s over IPv6. `curl` worked because it falls back to IPv4 by itself, and the Mac has no IPv6 address on
this network. The daily cap was not involved. Turning IPv6 off on the Pi's Wi-Fi until the next reboot fixed it:

```bash
sudo sysctl -w net.ipv6.conf.wlan0.disable_ipv6=1
```

## How it compares with full-duplex and commercial systems

The published numbers below are from the Full-Duplex-Bench papers and one model card, copied from the sources listed
at the end. They are not our measurements, and they are not like for like:

- fd-benchmark plays a fixed-seed sample of the authors' clips, five per group here; the papers score every clip.
- Ours is one run on a laptop over Wi-Fi with cloud STT, LLM and TTS. The papers ran each system once, on
  their own setups, at the time of writing (2025 and early 2026).
- The papers report one latency per system; ours shows the median and the mean.
- The course's false turn-taking rate is new: no published system has a number for it.
- Our judge is GPT-4o through OpenRouter. fd-benchmark notes that the paper's interruption-scoring code used
  gpt-4-turbo, so the judge scores compare closely but not exactly.
- PersonaPlex's figures are NVIDIA's own, from its model card, and its interruption latency is the time for the
  agent to stop speaking, not to start the answer.

### Full-Duplex-Bench v1

| System | Kind | Pause TOR synthetic (lower) | Pause TOR Candor (lower) | Turn-taking TOR (higher) | Turn-taking latency, s | Interruption TOR (higher) | Interruption judge score, 0 to 5 (higher) | Interruption latency, s |
|---|---|---|---|---|---|---|---|---|
| **ME3 (Mac, 5 rounds)** | cascaded, cloud | **0.00** | **0.20** | **1.00** | 2.35 mean (2.51 median) | **1.00** | **4.8** | 1.92 |
| dGSLM | full-duplex, research | 0.934 | 0.935 | 0.975 | 0.352 | 0.917 | 0.201 | 2.531 |
| Moshi (Kyutai) | full-duplex, open | 0.985 | 0.980 | 0.941 | 0.265 | 1.000 | 0.765 | 0.257 |
| Freeze-Omni | speech LLM, open | 0.642 | 0.481 | 0.336 | 0.953 | 0.867 | 3.615 | 1.409 |
| Gemini Live (Google) | commercial | 0.255 | 0.310 | 0.655 | 1.301 | 0.891 | 3.376 | 1.183 |
| PersonaPlex 7B (NVIDIA, self-reported) | full-duplex, open | 0.358 | 0.431 | 0.908 | 0.170 | 0.950 | 4.290 | 0.240 (time to stop) |

On these five clips per group, ME3 waits out pauses and takes turns at least as well as every listed system, since
Flux's end-of-turn model decides when the user is done from the speech itself, not from silence alone. Five
clips cannot separate 1.00 from dGSLM's 0.975 on turn-taking, but the pause gap to the full-duplex models is large. Its weakness is speed: native full-duplex models (Moshi,
PersonaPlex) answer in 0.17 to 0.27 s, about ten times faster, and Gemini Live in 1.3 s. On interruption latency ME3
(1.92 s) is slower than Moshi, Gemini Live and Freeze-Omni, and faster than dGSLM. On what she says after the
interruption, the GPT-4o judge gives ME3 4.8 of 5, the highest in the table, likely because a full text LLM answers
the new request better than a speech model does (not tested).

### Full-Duplex-Bench v1.5: stop and response latency

In seconds, lower is better for both. The paper gives averages; ME3's are medians. Its means are within 0.1 s of
them, except two response latencies: interruption 2.35 s and background speech 2.38 s.

| System | Interruption stop / response | Backchannel stop / response | Talking to others stop / response | Background speech stop / response |
|---|---|---|---|---|
| **ME3 (Mac, 5 rounds)** | 0.62 / 2.13 | 0.48 / 2.24 | 0.90 / 1.93 | 0.60 / 2.66 |
| GPT-4o Realtime (OpenAI) | 0.23 / 1.50 | 0.21 / 1.32 | 0.18 / 1.16 | 0.18 / 1.26 |
| Freeze-Omni | 1.42 / 1.35 | 0.66 / 2.16 | 1.39 / 1.32 | 0.98 / 1.60 |
| Moshi | 1.16 / 1.47 | 0.42 / 3.00 | 0.87 / 2.38 | 0.54 / 1.62 |
| Gemini Live | 2.20 / 2.62 | 0.66 / 2.45 | 1.69 / 1.78 | 0.95 / 2.38 |
| Amazon Nova Sonic | 2.25 / 2.75 | 0.64 / 1.45 | 1.77 / 2.04 | 1.05 / 2.76 |

On a real interruption ME3 stops in 0.62 s, faster than every listed system except GPT-4o Realtime (0.23 s). In
the other three scenarios Moshi stops about as fast or slightly faster (0.42 to 0.87 s against ME3's 0.48 to 0.90 s),
and GPT-4o Realtime stops in about 0.2 s throughout. ME3's response latency (1.9 to 2.7 s) is in the slow group with
Gemini Live and Nova Sonic; GPT-4o Realtime answers in 1.2 to 1.5 s.

### Full-Duplex-Bench v1.5: behaviour

The paper's labels, as shares of trials. The desired label is Respond for an interruption and Resume for the other
three.

| System | Interruption: Respond | Backchannel: Resume | Talking to others: Resume | Background speech: Resume |
|---|---|---|---|---|
| **ME3 (Mac, 5 rounds, n=5 each)** | 0.80 | 0.40 | 0.00 | 0.20 |
| GPT-4o Realtime | 0.78 | 0.70 | 0.02 | 0.04 |
| Freeze-Omni | 0.72 | 0.80 | 0.25 | 0.25 |
| Moshi | 0.50 | 0.06 | 0.19 | 0.07 |
| Gemini Live | 0.33 | 0.93 | 0.99 | 0.30 |
| Amazon Nova Sonic | 0.24 | 0.98 | 0.90 | 0.98 |

The papers show a trade-off that ME3 also has: the systems that respond best to a real interruption (GPT-4o
Realtime, Freeze-Omni) also answer side talk and background speech, and the ones that ignore them respond less to a
real interruption. Nova Sonic ignores all three overlaps; Gemini Live ignores side talk but answers background speech
70% of the time. ME3 sits with GPT-4o Realtime: it responds to a real interruption as often (0.80 against 0.78),
and like it, it answers side talk and background speech instead of resuming. Its backchannel Resume (0.40) is
below every system except Moshi.

### Where ME3 stands

- **At least as good as the listed systems** on pause handling and taking the turn (v1), on what it says after an
  interruption (judge score 4.8, Respond 0.80), and quick to stop for a real interruption, second only to GPT-4o
  Realtime.
- **Slower than all of them** to start an answer: about 2 s, against 0.2 to 0.3 s for native full-duplex models and
  1.2 to 1.5 s for GPT-4o Realtime. The work for this is in `TODO.md` under Latency.
- **Like GPT-4o Realtime** in answering speech that is not meant for it. Addressing (ADR-016) and duck-then-decide
  barge-in are built for this and stay off: on the A/B runs above, neither helped, because the trials keep her in
  addressing's ACTIVE state and ducking delays the stop.
- **Not measured here:** tools. ME3 runs MCP tools behind its own policy at about $0.22 a round of this benchmark,
  and it runs on a Raspberry Pi: the Pi run above meets every class target in the course table. GPT-4o Realtime and Gemini
  Live also call functions; Full-Duplex-Bench v1 and v1.5 do not test tool use (v3 does).

## Not done yet

- More rounds on the Pi: one round gives two barge-in trials and one trial per Full-Duplex-Bench group.
- The Flux eager end-of-turn A/B run (ADR-045).
- Classmates should compare runs with the same rounds and judge.

Run folders stay on the Mac in `fd-benchmark/runs/`, which git ignores. Their recordings hold the Candor and
Full-Duplex-Bench clips (CC BY-NC 4.0, coursework only) and are not committed.

## Sources

- Lin et al., Full-Duplex-Bench (v1), arXiv 2503.04721v3, Aug 16, 2025, Table III:
  https://arxiv.org/abs/2503.04721
- Lin et al., Full-Duplex-Bench v1.5, arXiv 2507.23159v4, Apr 26, 2026, Table 2: https://arxiv.org/abs/2507.23159
- NVIDIA PersonaPlex-7B-v1 model card (released Jan 15, 2026), FullDuplexBench table:
  https://huggingface.co/nvidia/personaplex-7b-v1
- fd-benchmark `docs/METHOD.md` at `293ba44`, for the rules and the course's added rates.
