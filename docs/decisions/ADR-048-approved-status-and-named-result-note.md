# ADR-048: Say "approved" plainly while a held action waits, and name the tool in the result note

Date: Nov 6, 2026. Status: approved by Quiel, Nov 6, 2026.

## Context

In run `20261106-232910-47c4a4` a confirmed `send_message` to a contact was really sent through the Mac bridge (`sent: true`), but the agent told the user twice that it was still waiting for confirmation.

The timeline:

- 30.7 s: the user said "Uh, yes. Send it." The policy confirmed it and wrote "The user said yes. It runs in 5 seconds unless they say cancel." into the tool message.
- 41.9 s: the undo window opened only when the agent's reply ended (ADR-011), so the send was due about 17 s after the yes, not 5 s.
- 47.1 s: the user asked "Can you send it now?" The LLM request started from the note that said "runs in 5 seconds", 17 s after the yes.
- 47.4 s: the send committed, 0.3 s after that request began.
- 48.2 s: the ADR-023 result note started one more reply. The model still said it could not confirm the send, probably trusting its own reply of a second earlier.

The system prompt calls the harness "the device", which is where "waiting for confirmation on the device" came from. The event log kept no reply text, so the wording is from the user's report, and the cause is inferred from timing.

## Decision

1. The confirmed note says the action is approved, that nobody needs to confirm anything more, that it runs by itself a few seconds after the next reply ends, and that it may already have run. If asked, the model should say it is about to happen or has happened, never that it waits for a yes.
2. The result note names the tool and says it succeeded, is done, and that any earlier reply saying it was waiting is out of date. It stays fixed text plus the tool name from our own policy, so tool output still never gets the user role (ADR-023).
3. `EventObserver` logs `reply_text` at the end of each LLM response that carried text, and `bench.run_report` shows it under each turn.

The undo window still opens when the reply ends, so the user hears "say cancel" before the countdown starts.

## Alternatives

- **Start the undo window at the yes.** Fixes the 17 s delay, but the window could close while the agent is still telling the user how to cancel.
- **Put the tool output in the note.** Rejected in ADR-023: fetched or file text would arrive as if the user said it.
- **Change the system prompt.** Would need a `PROMPT_VERSION` bump and breaks comparisons with earlier runs. The notes are enough to try first.

## Consequences

- Tested on fakes only. No live model has run on the new notes yet, so the fix is unproven until the next live send.
- Reply text in `logs/` may hold what the user asked about. `logs/` is already private and not committed (CLAUDE.md rule 8), and transcripts were already logged.
- The note is about 20 words longer, a few more prompt tokens on each held tool.

## Sources

- Pipecat 1.13.0, installed package: `pipecat/frames/frames.py` (`LLMTextFrame`, `LLMFullResponseEndFrame`, `LLMMessagesAppendFrame`)
- ADR-011, ADR-023 in this folder
