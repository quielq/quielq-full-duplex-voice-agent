# ADR-029: A separate USB mic and speaker on the Pi

Date: Nov 2, 2026. Status: decided by Quiel on Nov 2, 2026. Config and checks built, not run on a Pi.

## Context

The review plans a hardware-AEC speakerphone for Phase 2. Quiel will use a separate USB microphone and USB speaker. They have no echo cancelling of their own, and each runs on its own clock.

## Decision

- `pi.yaml` turns on `audio.echo_cancel`, the WebRTC AEC from ADR-013. PipeWire echo-cancel and then an RMS gate remain the fallbacks.
- `audio.input_device` and `audio.output_device` take name substrings, as before, and are set once the hardware is here.
- `local_transport` refuses a named device that cannot run at our rate, with a message that names the fix. A raw USB device often offers only 44.1 or 48 kHz, and the `pipewire` or `default` device resamples.
- `python -m harness.audio_io` now shows whether each device supports our rates.

## Measurements

`bench/echo_probe.py` on the Pi on Nov 2, 2026, USB mic and Dell AC511 SoundBar through PipeWire, no cloud calls, speaker at 84%, nobody talking. The mic is hot, at about -10 dB.

| Run | Result |
|---|---|
| Clean recording, 25 s | 8 raw onsets to 0 at hints 0 and 30 ms. Measured lag 30 ms. PASS |
| `--talker`, hint 30 ms | One of two talkers caught, 1.45 s after it began, plus a false onset at 3.78 s. FAIL |
| `--transport --repeat 10`, 250 s | Playback stretch 1.00, no write over 100 ms. 18 onsets against 75 raw, 3.07 s of speech against 233.86 s. 307 reference underruns. Level after cancelling about -52 dB early and -37 to -48 dB later |

The talker comes from the same speaker, so it stands in for a person, and the real barge-in test is live. The long run shows no runaway drift, but the blips mean the live echo test is still open. Temperature went from 59 to 75 C over these runs, so the Pi needs a check on cooling.

First live run on the Pi, `20261102-111258-a686c7`, Flux, Haiku 5.5, Cartesia, echo cancelling on, $0.05: three turns, all three transcripts were Quiel's own words, two tools ran, and Silero fired twice during replies without starting a turn. Replies were 3.0 and 3.4 s with a tool and 1.4 s without. No reply ran longer than about 7 s and nobody talked over one, so the 60 s echo test and real barge-in are still open. Temperature reached about 78 C.

## Alternatives

- **Buy a speakerphone:** the most reliable echo result, but not the hardware chosen.
- **PipeWire echo-cancel first:** runs before PortAudio, and is a fair second choice. The harness AEC is the one with measurements behind it.

## Consequences

- Two USB clocks drift, which WebRTC AEC tolerates only to a point. This is the main risk, and it can only be measured on the hardware.
- The distance between speaker and mic now matters. Keep them apart and aim the speaker away.
- `livekit` has Linux aarch64 wheels, but its CPU cost and fit on the Pi are unchecked (ADR-013).
- Barge-in over a loud speaker depends on the AEC, so `immediate` versus `duck` must be chosen on this hardware.

## Sources

- https://docs.livekit.io/reference/python/livekit/rtc/apm.html
- https://people.csail.mit.edu/hubert/pyaudio/docs/#pyaudio.PyAudio.is_format_supported
