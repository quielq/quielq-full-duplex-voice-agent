# ADR-044: Run the class benchmark through its launcher, with a socket input mode

Date: Nov 6, 2026. Status: accepted (Quiel approved the plan on Nov 5).

## Context

The course benchmark, fd-benchmark (https://github.com/trueshotjames/ai231-me3-full-duplex-benchmarking), is what we
report: the class-benchmark. Our own benches stay as the self-benchmark. fd-benchmark adapts Full-Duplex-Bench v1 and
v1.5 and talks to the assistant over a WebSocket ("direct feed"). Its launcher, `launch_assistant.py`, builds a Pipecat
transport and calls an entry's `build(transport, log_dir)`. Its own entry fits the course layout (`assistant/main.py`),
which we do not have, so it asks other projects to write their own entry (its `docs/GUIDE.md`, step 4B).

`run_bot` builds its own transport for each input mode, and the mic mode's capture stamps hook PyAudio callbacks that
a socket transport does not have.

## Decision

- `harness/bot.py` gets an `fdbench` input mode. It takes the launcher's transport and builds the same front and back
  of the pipeline as the mic: input voice tap, mic gate, Silero VAD, STT, policy gate, filler, clips and output. The
  output voice tap runs with zero device latency, as with the null output in wav mode. There are no capture stamps,
  since frames arrive without a sound card. A new `on_worker` hook hands out the worker so the launcher can stop it.
- `bench/fdb_adapter.py` is the entry (it replaces the old stub). The launcher finds it through its own
  `FDBENCH_ENTRY` variable, so fd-benchmark stays unchanged. Profile, stages and servers come from `ME3_PROFILE`,
  `ME3_STAGES` and `ME3_SERVERS`, so every stage stays fake unless a run names live ones.
- In this mode, echo cancelling and the self-echo filter are off: there is no speaker and mic, so there is no echo.
- Each session's task servers get an empty temporary data folder. On the first fake run, a `set_timer` wrote into
  the real `data/clock.json` and the timer rang through later trials. The cost ledger stays in the real data folder,
  so the daily caps hold.
- fd-benchmark is cloned into `fd-benchmark/`, which `.gitignore` lists. Its run folders hold dataset recordings
  (CC BY-NC 4.0) and are never committed.

## Alternatives

- **Write `fd-benchmark/assistant_entry.py` in the clone.** Their default place, but git ignores the clone, so the
  entry would not be in our public repo or reach the Pi through `git pull`.
- **Virtual audio cables** (their `docs/SETUP.md`). The bot stays on the mic path, but it needs BlackHole or PipeWire
  loopbacks on each machine, and it adds device latency the papers do not have.
- **Our own runner around the Full-Duplex-Bench scripts** (the old stub's plan). It would not be the course's numbers.

## Consequences

- A fake TTS plays tones, which fdbench's Silero VAD does not count as speech. After 5 trials with no reply, fdbench
  stops and waits for a key, so a fake round only proves the audio path and then stalls. Only live TTS gives verdicts.
- `FDBENCH_ENTRY` must be an absolute path: the launcher runs from another folder and otherwise falls back to the course
  entry, which stops with "project not found".
- Live on the Mac, Nov 6: one round as a setup check (`20261106-093545-e87fc7`, $0.23), then five rounds
  (`20261106-102804-7e95d7`, 75 trials, $1.08). Results and the comparison with published systems are in
  `docs/class-benchmark.md`.
- Not run on the Pi yet. There the Mac runs fdbench and the Pi runs the launcher (the guide's steps 8 to 11). The
  Pi's stats probe reads `/proc` without psutil, and `--stats-procs` needs our server modules
  (`servers.voice_listen`, `servers.voice_speak`, `servers.tasks`).

## Sources

- fd-benchmark at `293ba44`: `launch_assistant.py` (`load_entry`, `FDBENCH_ENTRY`, `run_once`),
  `adapters/pipecat_fdbench.py`, `adapters/assistant_entry_template.py`, `fdbench/runner.py`
  (`NO_REPLY_PAUSE_AFTER = 5`), `docs/GUIDE.md`, `docs/METHOD.md`.
- Pipecat 1.13.0, installed: `pipecat/transports/websocket/client.py`.
