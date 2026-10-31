# ADR-013: WebRTC echo cancellation in the harness for speaker runs

Date: Oct 31, 2026. Status: built and off by default. Not yet tested in a live conversation.

## Context

The review says headphones in Phase 1 and leaves echo to Phase 2. Quiel wants to test on MacBook speakers now, because the demo needs speakers and this is a rehearsal for the Pi. A live run on speakers on Oct 31 had the agent transcribing and answering itself.

The review's order for the Pi is a hardware-AEC speakerphone, then PipeWire's WebRTC module, then an RMS gate. PipeWire does not run on macOS.

Pipecat 1.13.0 has no AEC, but its transports take an `audio_in_filter` and an `audio_out_filter`.

## Decision

`harness/echo_cancel.py` runs WebRTC AEC from the `livekit` package (`rtc.AudioProcessingModule`), enabled by `audio.echo_cancel` or `--echo-cancel`, off by default in both profiles.

* The output filter copies audio just before it plays, after any `DuckGain`.
* The input filter takes 10 ms of queued reference for each 10 ms of mic audio, because output arrives in 40 ms bursts and WebRTC needs both in exact 10 ms frames.
* The queue is capped at 1 s.
* `echo_cancel` also satisfies the built-in speaker guard from commit `fac9ef9`.

This is a departure from the review's Phase 1 headphone rule. Headphones remain the default.

## Measurements

`bench/echo_probe.py`, no cloud calls, MacBook Air speakers and mic at 81% volume, about 30 s of `say` speech, nobody talking. Silero with the profile's settings. One recording per run, replayed at each delay hint.

| Run | Path | Silero onsets, raw | Onsets, cancelled | Level, raw to cancelled |
|---|---|---|---|---|
| 1 | Lockstep 10 ms frames | 8 | 0 at hints 0 to 150 ms | -26.6 to -56.9 dB |
| 2 | `EchoCanceller`, 40 ms bursts | 8 | 1 at all hints, at 0.58 s | -26.6 to -42.1 dB |

Playback starts at 0.5 s, so the onset in run 2 is the canceller adapting to the first speech. It lasted 30 to 60 ms at hints up to 100 ms and more at longer hints. Nothing fired after it. The level figure covers the whole clip, so that transient may account for much of the gap between the runs, but this was not separated.

Talker run (`--talker`, hint 50 ms): a second voice played from the same speakers and left out of the reference, twice.

* Silero caught both, 0.94 s and 1.55 s after the talker began. The sentence takes about 2 s.
* One false onset, at 0.61 s, the same startup transient.

Only total cancelled speech time was recorded for that run: 0.74 s.

## Alternatives

* **macOS voice processing I/O:** not reachable from PyAudio, and it would not carry to the Pi.
* **`aec-audio-processing`, `webrtc-audio-processing`:** the first has no macOS wheels listed, and the second is unmaintained.
* **Half-duplex gate:** reliable, but gives up barge-in.

## Consequences

* The first bot speech in a session may leave a brief blip. In the Oct 31 run, interruptions came from Flux turn events, not VAD, so a blip may not interrupt. The live test decides this.
* Barge-in on speakers is slow, at 0.9 to 1.5 s against the 100 ms duck target. A real person sits apart from the speakers, so this may differ, but expect cancellation to delay detection during double-talk.
* Probe results come from a clean recording. Live buffering could be worse. `echo_delay_ms: 50` is a hint only.
* `livekit` publishes Linux aarch64 wheels, so this could run on the Pi. That is untested, and neither CPU cost nor glibc fit is checked there.
* A pass on the Mac does not cover room acoustics, volume for an audience, or the speakerphone.

## Sources

* https://docs.livekit.io/reference/python/livekit/rtc/apm.html
* https://pypi.org/project/livekit/
* Installed source: `pipecat/transports/base_input.py`, `base_output.py`, `local/audio.py`
