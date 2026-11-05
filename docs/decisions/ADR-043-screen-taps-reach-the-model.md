# ADR-043: Taps on the face reach the model before the next turn

Date: Nov 5, 2026. Status: accepted (Quiel approved the fix on Nov 5).

## Context

A tap on the robot face runs a tool through `ToolPolicy.run_from_screen` (ADR-038). The model never saw it. In the Nov 5 live run `20261105-195758-061828`, Haiku played "Welcome to the Black Parade", a tap on next track changed it to "I Don't Love You", and when asked what was playing Haiku named the old song from its context without calling `get_now_playing`. Lights and the thermostat can go stale the same way.

## Decision

- `harness/screen_notes.py` holds one note per tap that ran: the tool, its arguments, and its result, cut to 300 characters, at most eight notes. A tap refused by the policy, or whose tool returned an error, changed nothing and adds no note.
- `ScreenNotes` sits just before the user aggregator. When the next final transcript arrives, it first sends the notes as one user message with `run_llm=False`, marked as not said aloud. The aggregator merges it with the transcript into one user turn, so the model sees the taps and the question together. A scripted text turn takes the notes the same way.
- `get_now_playing` now says that the song changes by itself and from the screen, so the model calls it every time instead of answering from memory. This also covers a song that ends on its own, which no tap note catches.

Nothing on the screen gains any power: the note only reports what `run_from_screen` already ran.

## Alternatives

- **Append the note at the moment of the tap.** Pipecat 1.13's user aggregator adds an appended message to the context at once (`_handle_llm_messages_append`). During a tool call that puts a user message between the model's tool request and its result, which Anthropic refuses, and during a reply it lands before the words being spoken. Waiting for the next transcript avoids both.
- **Only change the tool description.** Cheap, but it relies on the model, and lights and the thermostat would still go stale.
- **Speak each tap aloud.** Noisy, and still leaves the context out of date.

## Consequences

- A tap followed by a question costs a few more prompt tokens on that turn only.
- A transcript dropped before the aggregator (the self-echo guard, or the policy answering "cancel" itself) keeps the notes for the following turn.
- Checked on fakes only: `tests/test_screen_notes.py`.

## Sources

- Pipecat 1.13.0, installed: `pipecat/processors/aggregators/llm_response_universal.py` (`LLMUserAggregator._handle_llm_messages_append` adds messages at once), `pipecat/adapters/services/anthropic_adapter.py` (consecutive messages with the same role are merged).
- ADR-038 (face and screen controls), ADR-011 (tool policy).
