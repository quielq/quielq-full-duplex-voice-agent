# ADR-036: The standardized latency measurement, from sample-level audio marks

Date: Nov 4, 2026. Status: built and tested on fakes and wav runs. No live run measured yet.

## Context

Spec item 6 asks for "a standardized latency measurement method", and it is graded. The review (`docs/architecture.md`, Benchmark harness) defines end-to-end latency as the end of user speech to the first non-silent output sample at the device, and says headline numbers come from audio timestamps while Pipecat metrics frames are attribution only. The run protocol asks for warm-up turns dropped, 30 or more repeats, and p50, p90, and p95.

Until now `bench/latency_probe.py` was a skeleton. The live numbers so far (p50 1.84 s over 59 replies, 1.08 s with no tool and 2.31 s with one) run from `end_of_turn` to `first_audio_out`, both framework events. They leave out the end-of-turn wait, and `first_audio_out` is Pipecat's `BotStartedSpeakingFrame`, which fires when audio is queued, not when the first loud sample plays. The paced null output already logged an acoustic first sample, but it could not tell the thinking phrase (ADR-009, ADR-033) from the reply, and nothing did the same on a real device or on the input side.

## Decision

**The headline** is the time from the last voiced input sample before end of turn to the play time of the first voiced TTS sample after it. Both ends are thresholds on the PCM itself, logged by `harness/audio_marks.py`:

- **Input** (`InputVoiceTap`, right after the mic or the scripted wav source, after the echo canceller). `user_audio_end` carries `audio_t_ns`, the time of the last sample whose magnitude reaches the threshold, once the signal has stayed under it for 300 ms, so a pause inside a sentence is not an end. The threshold is an int16 peak of 500 (about -36 dBFS, the same as `speech_onset_s`). On a live mic it rises to four times a tracked noise floor, and never falls below 500. Each frame carries `capture_ns`, the time of its first sample: the scripted source stamps its pacing slot, and on the mic `stamp_capture` takes the time in the PyAudio callback and subtracts the frame length and the stream's reported input latency.
- **Output** (`OutputVoiceTap`, wrapping `write_audio_frame` on PyAudio's output or the null output). `reply_audio_onset` carries the play time of the first sample of a run of TTS audio at a peak of 64 or more (about -54 dBFS). Play time models the device as a queue: a chunk starts when the one before it ends, or after an underrun at the write call plus the stream's reported output latency. A new run starts after 300 ms below the threshold.
- **The thinking phrase never counts.** Pipecat cuts output audio into chunks that keep the class of the frame they came from (`handle_audio_frame` in `base_output.py`), so a `TTSAudioRawFrame` chunk is reply audio and a plain `OutputAudioRawFrame` chunk is the clip. The clip is logged as `local_audio_onset` and reported apart. The thinking phrase now pads each clip to whole 40 ms chunks, so no chunk mixes a clip with the reply after it. A reply chunk that follows a clip chunk is still marked `boundary`, and the report counts them.

**Pairing is by time, not turn id.** A turn is each first `end_of_turn` per turn id. Its speech end is the last `user_audio_end` stamped before it and after the previous end of turn. Its reply is the first `reply_audio_onset` after it and before the next `user_turn_start`, so a barge-in before any reply audio leaves the turn unmeasured as `no_reply`. A turn with no input mark, such as a log from before this change, is `no_speech_end`. Tool turns are those with a `tool_call_start` in the same window.

**Statistics.** p50, p90, and p95 by linear interpolation (numpy's default), with n, mean, min, and max, for all turns, no-tool turns, and tool turns. The first three turns of each session (each event log) are dropped as warm-up, which `--warmup` can change. Several logs or run ids go into one report. The report warns when the runs mix stage sets.

**Attribution.** The old per-stage deltas from framework events stay, plus two from the marks: `end_of_turn_wait` (speech end to end of turn) and `end_of_turn_to_reply_audio`. Injection events now join the turn of the next end of turn, so multi-turn wav runs attribute every turn and not only the first.

**Cost per turn** sums the cost ledger's entries by run id, falling back to the run's `run_end.session_usd`, and divides by user turns, warm-up included because they were paid for.

**Report.** A table on stdout and the full report, with every turn, in `logs/latency-<time>.json`. `bench/latency_bench.py` runs N scripted wav turns per session on fakes, half with a tool and half without, spoken by macOS `say` and cached under `data/`. A new `bot_quiet` segment mode starts each turn once the bot has replied and been quiet, so turns never barge in on each other. `--stages` with any real provider refuses without `ME3_LIVE=1`.

## Alternatives

- **Framework events (`end_of_turn` to `BotStartedSpeakingFrame`).** What the live numbers used. It leaves out the end-of-turn wait and the device queue, and the review rules it out for the headline.
- **`injection_speech_end` as the start.** Exact for wav runs but includes the trailing silence `say` adds (about 65 ms on our clips), and it does not exist on a live mic. Kept as an attribution stage.
- **A loopback cable or second recorder at the speaker.** The only way to include the speaker's own hardware delay beyond what PortAudio reports. It needs hardware and a recorder, so it stays optional, as the review says.
- **Telling the clip from the reply by a byte count queue at the output.** Exact inside a chunk, but Pipecat pads the last chunk of a reply with silence it never announces, so the queue drifts. Chunk class plus padded clips is simpler and exact in practice.
- **Nearest-rank percentiles.** Equivalent at 30 or more turns; linear interpolation matches numpy and most tools.

## Consequences

- Live runs need no new flags: the taps are on in mic and wav mode, and `uv run python -m bench.latency_probe --last N` reads them. Logs from before this change give attribution only.
- On fakes the headline is about 0.3 to 0.4 s, nearly all of it the end-of-turn wait, which the old live figures left out. Expect live numbers above the 1.84 s p50 for the same reason.
- The device latency PortAudio reports is its estimate, not a measurement, and each run logs it as `audio_out_latency`. Bluetooth headphones add delay it may not see.
- The input threshold is a guess for a quiet room. A noisy room ends speech later by raising the floor, which makes latency look shorter, so the threshold is logged with each mark.
- Stop latency (interrupter onset to output silence) can use the same marks but is not built here. Cost per successful task needs the task judge and is not built here either.
- Unsolicited speech such as a timer firing inside a turn window would count as that turn's reply. The bench uses ten-minute timers so none fires during a session.

## First live headline, Nov 4

`20261104-202417-64248a`: Mac, speakers with `--echo-cancel`, `stt=voice_listen,llm=anthropic,tts=voice_speak`, 18 turns, 3 warm-up turns dropped, 14 measured. One run, so a first look, not the benchmark.

| Turns | n | p50 | p90 | max |
| --- | --- | --- | --- | --- |
| All | 14 | 3,040 ms | 4,706 ms | 5,849 ms |
| No tool | 10 | 2,781 ms | 3,360 ms | 3,604 ms |
| Tool | 4 | 4,405 ms | 5,642 ms | 5,849 ms |

Attribution at p50: end-of-turn wait 1,306 ms, LLM first token 782 ms, TTS first byte 376 ms, tool call 384 ms. The end-of-turn wait the old figures left out is the largest single part. The thinking phrase played on 4 of 15 turns, 2.6 s after speech at p50, on every tool turn that took over 1500 ms after end of turn, including `get_time` and `set_timer`, because a tool turn needs two LLM calls. Cost $0.012 a turn.

## Pi benchmark, Nov 5

`bench.latency_bench --profile pi --sessions 4 --turns 10 --stages stt=voice_listen,llm=anthropic,tts=voice_speak`, Raspberry Pi 5, scripted wav turns (synthetic `say` speech, clock server only, so the tool turns are `set_timer`, `get_time`, and `list_timers`). Runs `20261105-223356-74b35c`, `20261105-223535-19209f`, `20261105-223718-fd171a`, and `20261105-223855-3d2f74`, report `logs/latency-20261105-224036.json` on the Pi. 40 turns, 3 warm-up turns dropped per run, 28 measured, $0.21 ($0.0052 a turn).

| Turns | n | p50 | p90 | p95 | max |
| --- | --- | --- | --- | --- | --- |
| All | 28 | 2,563 ms | 3,052 ms | 3,121 ms | 3,366 ms |
| No tool | 14 | 2,474 ms | 2,820 ms | 2,890 ms | 3,010 ms |
| Tool | 14 | 2,674 ms | 3,121 ms | 3,208 ms | 3,366 ms |

Attribution at p50: end-of-turn wait 1,108 ms (end of turn 1,046 ms after speech), LLM first token 657 ms, TTS first byte 281 ms, tool call 13 ms, VAD release 182 ms. The thinking phrase played on none of the 28 turns. The end-of-turn wait is still the largest part, as on the Mac.

Against the Nov 4 Mac run: p50 2.6 s against 3.0 s. The tool turns are much closer to the no-tool turns here (2.7 s against 4.4 s), because these clock tools take 13 ms and the Mac run's tools were slower and fewer. So this is the headline for scripted turns with quick tools, not for weather or search turns. 28 kept turns is two short of the 30 the benchmark plan asks for, and it is one time of day.

## Sources

- `docs/architecture.md`, Benchmark harness: Definitions and Run protocol.
- Pipecat 1.13.0, installed: `pipecat/transports/base_output.py` (`handle_audio_frame` keeps the frame class per chunk, `_enqueue_flushed_audio_buffer` pads with silence), `pipecat/transports/local/audio.py` (PyAudio callback input, blocking writes in an executor), `pipecat/transports/base_input.py` (the input filter rewrites `frame.audio` in place).
- PyAudio `Stream.get_input_latency` and `get_output_latency`: https://people.csail.mit.edu/hubert/pyaudio/docs/
- PortAudio `PaStreamInfo` latency fields: https://www.portaudio.com/docs/v19-doxydocs/structPaStreamInfo.html
- numpy `percentile`, linear method: https://numpy.org/doc/stable/reference/generated/numpy.percentile.html
