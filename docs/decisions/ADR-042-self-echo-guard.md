# ADR-042: Do not answer the bot's own words heard through the speakers

Date: Nov 5, 2026. Status: accepted (Quiel approved the fix on Nov 5).

## Context

On speakers, WebRTC AEC (ADR-013) removes most of the bot's voice but not all. In the Nov 4 Mac run `20261104-202417-64248a`, the echo canceller's output sat at about -38 dB while the bot played at about -17 dB, and enough of "I can't delete" got through that Flux started a user turn. The bot cut itself off and then answered its own words. The Pi uses a separate USB mic and speaker with the same AEC (ADR-029), so the demo can hit the same case.

## Decision

`harness/self_echo.py` adds two processors, on only when `audio.echo_cancel` is on (the Pi profile, or `--echo-cancel` on the Mac):

- `SpokenTap`, after the TTS, records the words the bot sends to the speaker.
- `SelfEchoFilter`, before the user aggregator, drops a final transcript when the turn began while the bot was speaking, has at least three words, and those words appear in order and next to each other among the bot's words of the last 15 s. A transcript with an allowlist word (stop, wait, no, cancel, pause) is always kept. A drop logs `self_echo`.

The turn then ends with no transcript, so Pipecat's empty turn recovery runs once, as for any unrecognised sound that cut the bot off: on fakes the bot says "Sorry, could you repeat that?" With addressing on, that recovery is off and the bot stays quiet.

## Alternatives

- **Stop the cut-off itself:** Flux starts the turn from audio before any transcript exists, so nothing can be matched in time. Raising the bar for a turn start over bot speech belongs with the barge-in work (duck mode, ADR-007) and the Pi echo test.
- **Prompt the LLM to ignore its own words:** the LLM would still run, and it cannot tell an echo from a user quoting it.
- **Match loosely (any shared words):** a person answering "the kitchen light" to a question that offered it would be ignored.

## Consequences

- It does not stop the bot cutting itself off. It stops the bot answering itself, which was the visible failure. Headphones on the Mac, and the 60 s echo test on the Pi, remain the real protection.
- A person who interrupts with three or more of the bot's exact recent words, none on the allowlist, is not heard for that turn and gets asked to repeat.
- With headphones it is off, since there is no echo to catch.
- Checked on fakes only: unit tests and a whole wav run where the bot's story words cut in.
- Live on Nov 5 (three Mac speaker runs, 27 turns, long replies such as a story and the planets): no `self_echo` drop and no cut-off from the bot's own words, so the guard had nothing to catch. Every interruption was the user. The drop path is still proven on fakes only.

## Sources

- Pipecat 1.13.0, installed: `pipecat/turns/empty_user_turn.py` and `pipecat/processors/aggregators/llm_response_universal.py` (`_maybe_recover_empty_user_turn`: an empty turn that interrupted the bot runs the LLM once with the default prompt).
- ADR-013 (WebRTC AEC), ADR-016 (addressing), ADR-029 (Pi audio).
