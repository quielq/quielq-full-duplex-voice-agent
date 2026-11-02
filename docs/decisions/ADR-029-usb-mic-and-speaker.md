# ADR-029: A separate USB mic and speaker on the Pi

Date: Nov 2, 2026. Status: decided by Quiel on Nov 2, 2026. Config and checks built, not run on a Pi.

## Context

The review plans a hardware-AEC speakerphone for Phase 2. Quiel will use a separate USB microphone and USB speaker. They have no echo cancelling of their own, and each runs on its own clock.

## Decision

- `pi.yaml` turns on `audio.echo_cancel`, the WebRTC AEC from ADR-013. PipeWire echo-cancel and then an RMS gate remain the fallbacks.
- `audio.input_device` and `audio.output_device` take name substrings, as before, and are set once the hardware is here.
- `local_transport` refuses a named device that cannot run at our rate, with a message that names the fix. A raw USB device often offers only 44.1 or 48 kHz, and the `pipewire` or `default` device resamples.
- `python -m harness.audio_io` now shows whether each device supports our rates.

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
