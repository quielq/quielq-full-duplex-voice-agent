# ADR-051: The face shows the question it is waiting on, and the project it is building

Status: accepted. Quiel approved the face work as part of the Nov 16, 2026 plan. Source: the robot face rules in ADR-027 and ADR-038.

## Context

In the demo the face showed a status word and one line of caption. Three things were hard to follow.

- When the agent held an action, the only sign was "Waiting for your answer". Someone new did not know what it was asking or what to say, and the 5 second undo window was invisible.
- A build ran for a minute with only "Writing index.html" flickering by. Nothing showed what had been made, and the published link was only spoken.
- A first-time user saw a robot and no idea what to ask.

## Decision

Three additions, all drawn from the event log the face already reads (`harness/ui.py`).

- **Question card**, under the face. It opens on the tool policy's `hold_confirm` and shows the harness's own read-back (`read_back`), never the model's words, with "Say yes or no". When the undo window opens (`undo_window_open`) it shows the same question with "Say cancel to stop it", a seconds count, and a bar that empties over the window. It closes on any end of the hold: committed, undone, declined, timed out, failed, or superseded.
- **Project card**, first on the Home tab. A `write_file` or `edit_file` inside a folder starts or updates it: the folder name and the last six files. `publish_site` marks it waiting for a yes, then publishing soon, then shows the link in large type once the publish commits.
- **Idle hint**, in the caption, only while nothing has been said this session: "Try saying ..." with six example requests, changing every 8 seconds.

The status dot also follows the activity: green while listening, amber while asking, and pulsing while working.

## Rules kept

- The cards display only. A tap on them does nothing, so the screen still cannot skip a question (ADR-038).
- The link shown is only one a real `publish_site` commit returned, and only on `*.github.io`. It is plain text, not a link, so a tap never opens a browser over the face.
- Pi cost: the cards redraw only when their data changes. The countdown is one `transform` transition per hold plus a text update once a second on the existing tick. The hint fades with `opacity`. No filters, no blur, no outside requests.
- Reduced motion turns off the countdown transition, the hint fade, and the dot pulse.

## Alternatives

- Speak the countdown: more speech on every held action, and the user already hears the read-back.
- Make the link tappable: on the Pi kiosk the page would cover the face, with no way back by touch (ADR-026, ADR-049).
- A QR code for the link, so others can open it on a phone: useful for a demo, but it needs an encoder in the page. Left for later.
- Show every tool step in a list: busy on a 7 inch screen, and the event log already has it.

## Consequences

- The project card follows one folder at a time. A job that writes to two folders shows the last one.
- The hint list names the demo requests, so it needs updating if the demo changes.
- Checked in the demo (`uv run python -m harness.ui --demo`) at 1024 by 600 and at phone width. Not yet seen on the Pi screen.
