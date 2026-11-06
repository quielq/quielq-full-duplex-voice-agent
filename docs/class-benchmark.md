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
  `fdbench` input mode runs the same VAD, Smart Turn, STT, LLM, TTS, tool policy and cost ledger as the mic.
- Two settings differ from a mic run: echo cancelling is off (the clips arrive as samples, so there is no echo), and
  each session's tools start from an empty data folder (so a timer from an earlier run cannot ring during a clip).
- Stages: Flux and Cartesia through our voice servers, and Claude Haiku 5.5, with the Mac profile. Addressing
  (ADR-016) is off, as it is by default.
- No LLM judge: the interruption relevance score and the v1.5 Respond / Resume labels are not scored. ASR for word
  counts is faster-whisper small. UTMOSv2 is not installed.

```
cd fd-benchmark
FDBENCH_ENTRY=$PWD/../bench/fdb_adapter.py ME3_PROFILE=mac ME3_STAGES=stt=voice_listen,llm=anthropic,tts=voice_speak \
  .venv/bin/python -m fdbench run --student quielq --rounds 1 --connect direct --where local \
  --judge-provider none --yes --recordings k --no-live
```

`FDBENCH_ENTRY` must be an absolute path, or the launcher falls back to the course layout and stops.

## Mac, Nov 6, 2026: one round

fdbench run `2026-11-06_09-35-44`, ME3 run `20261106-093545-e87fc7`. MacBook Air, 8 GB. One round is 10 clips, one per
group, so 14 trials with the v1.5 clean twins. It took about 7 minutes and cost $0.23 (Deepgram $0.04, Cartesia $0.18,
Anthropic $0.02).

**Each figure below rests on one clip.** The brackets are the benchmark's 95% intervals, and they show how little one
clip says. Read this round as a working setup, not a result.

### Full-Duplex-Bench v1 and the course rates

| Metric | Result | Better |
|---|---|---|
| TOR, pause handling (synthetic) | 0% (0/1) [0 to 79] | lower |
| TOR, pause handling (Candor) | 100% (1/1) [21 to 100] | lower |
| TOR, turn-taking | 100% (1/1) [21 to 100] | higher |
| Missed turn-taking rate | 0% (0/1) [0 to 79] | lower |
| False turn-taking rate | 100% (2/2) [34 to 100] | lower |
| of which side talk | 100% (1/1) | lower |
| of which TV or radio | 100% (1/1) | lower |
| Latency, turn-taking | 1.73 s (n=1) | lower, under 1 s |
| TOR, interruption | 100% (1/1) [21 to 100] | higher |
| Latency, interruption | 1.92 s (n=1) | lower |
| Judge score, interruption | not judged | higher |

### Full-Duplex-Bench v1.5, timing only

| Scenario (expected) | What she did | Stop latency | Response latency |
|---|---|---|---|
| Interruption (Respond) | paused and came back | 0.73 s | 1.92 s |
| Backchannel (Resume) | kept talking | none | none |
| Talking to others (Resume) | paused and came back | 0.57 s | 1.51 s |
| Background speech (Resume) | paused and came back | 0.60 s | 1.67 s |

The clean twins, the same turns with no overlap, were all answered, after 1.71 to 2.47 s. The Respond and Resume
labels need the judge, so these rows only say when she stopped and came back.

Voice change after the interruption (n=1, no t-test possible): words per minute -1.9, pitch mean -31.4, pitch SD +5.0,
intensity mean -2.1, intensity SD -0.6.

### What this round shows

- **She answers what is meant for her.** The turn-taking clip, the interruption and all four clean twins were
  answered, and the synthetic pause was waited out.
- **She also answers what is not meant for her.** Both not-addressed clips (side talk, TV or radio) got a reply,
  and in the side talk clip she started 2.4 s before it ended. With addressing off the agent replies to any speech.
  Addressing (ADR-016) is built for this, but has only run on fakes.
- **She cut into the Candor pause.** She spoke 3.6 s at 8.3 s into the clip, so end of turn fired inside the pause.
- **She stops for every overlap except a backchannel.** She went quiet 0.6 to 0.7 s after side talk and background
  speech, where the papers want her to keep going, and kept talking through "sure yeah". This matches immediate
  barge-in (duck mode is off by default).
- Latencies of 1.5 to 2.5 s are above the papers' 1 s mark. They include Flux end of turn, Haiku and the first
  Cartesia audio.

## Not done yet

- More rounds. The benchmark does not set a number; there are 30 clips per group. Classmates should compare runs with
  the same rounds and judge.
- The judge, added later on the same run folder with `fdbench score`.
- The Pi run, with the Pi as the remote assistant (the guide's steps 8 to 11).

Run folders stay on the Mac in `fd-benchmark/runs/`, which git ignores. Its recordings hold the Candor and
Full-Duplex-Bench clips (CC BY-NC 4.0, coursework only) and are not committed.
