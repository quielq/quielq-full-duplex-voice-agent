# ADR-038: A visual home panel, chat history, and touch controls on the face

Date: Nov 4, 2026. Status: built and tested on fakes and in the demo page only. Not measured on the Pi yet.

## Context

The robot face (ADR-027) showed the agent's mood and a plain text panel: one line per light, the thermostat setpoint as a number, the track name, and timers as text. For the demo, Quiel asked for:

- the chat history on screen;
- lights, the thermostat, timers, and alarms drawn as pictures, so the state reads at a glance from across a room;
- play, pause, and next buttons for music.

The same page runs on the Pi's 7 inch touch display (1024 by 600), and the Pi must not run hotter for it. ADR-027 said the mic mute was the only control, so that nothing on screen could bypass the tool policy.

## Decision

- **Layout.** A top bar holds the agent chip (ADR-039), the status, and the mute button. The face fills the left. The right column has two tabs, Home and Chat, sized for 1024 by 600. Below 720 px wide, or in portrait, everything stacks in one column with no horizontal scroll.
- **Lights** are one bulb tile per room. The bulb is filled by brightness and has rays when on.
- **The thermostat** is a half-circle dial from 16 to 30 °C, with the setpoint in the middle and a coloured mode badge: cool is blue, heat is orange, and anything else is grey.
- **Timers and alarms** each get a ring that empties as time runs out. Alarms also show the time of day.
  - The clock server now stores `created_at` on new items, so the ring knows the whole length.
  - An item without `created_at` shows a full ring.
  - Up to four are shown, plus "and N more".
- **Chat history.**
  - `UiState` keeps the last 40 entries: what the user said, each reply, a timer's spoken phrase, and agent switches.
  - A reply becomes an entry when it ends, at `audio_silent`, the next turn, or a tool call. While it streams it shows only in the caption.
  - The history is a separate SSE event (`event: history`), sent only when an entry is added. Streaming text therefore never resends the history, and the state frame stays small.
- **Music buttons.** Pause or play, and next.
  - The page posts `/api/music`. The bot calls `ToolPolicy.run_from_screen`, which runs `stop_music`, `resume_music`, or `next_track` through the same MCP handler and policy log as a voice request.
  - The screen may run only those three tools, only with no arguments, and only when their rule is `auto` with no undo window and no guard. Anything the policy would hold, ask about, or guard is refused and logged as `denied_screen`. So the screen can never skip a question the voice path would ask.
  - Each press is logged as `ui_action`.
  - Lights and the thermostat were display only at first. See the Nov 4 update below.
- **Cost on the Pi.**
  - The page is still one self-contained file with no outside requests.
  - CSS animates only `transform` and `opacity`, with no filters, blur, or canvas.
  - Each panel section is redrawn only when its data changes. Timer text and rings update once a second on the existing tick.
  - The talking mouth's 90 ms timer runs only while the agent talks.
  - The face's bob and loading animations pause after 60 s idle, and stop for `prefers-reduced-motion`.
  - On the server, device files are re-read only when their mtime or size changes, and no state is built while no page is open.
  - Measured in the demo page on the Mac: no long tasks over 8 s, and about 200 DOM nodes.
- **Shutdown fix.** An open page used to hold `runner.cleanup()` for aiohttp's 60 s shutdown timeout. Each event stream now gets a sentinel on stop. The UI tests went from 61 s to 1 s.

## Alternatives

- **One long page with no tabs:** it does not fit in 600 px next to the face, and scrolling a touch panel during a demo is clumsy.
- **Touch controls for lights and the thermostat:** left out at first because they were not asked for. Quiel asked for them later the same day (update below).
- **A chart or animation library:** an outside request, more to parse on the Pi, and no gain over a few SVG paths.
- **Send history in every state frame:** it is about 8 KB at 40 entries, sent up to ten times a second while text streams.

## Consequences

- ADR-027's "mute is the only control" now reads: mute, the music buttons, a tap on a light, the thermostat steps, all through the tool policy, and the agent picker and builder (ADR-039).
- Not measured on the Pi: the CPU and temperature check belongs to the Pi port (`bench/doctor.py` and `harness/system_stats.py` during a soak run with the face open).
- Pressing pause on the screen does not tell the LLM. If the user then asks what is playing, the model calls `get_now_playing`.

## Update, Nov 4, 2026: lights and thermostat by tap

Quiel asked for the lights and the thermostat to respond to taps.

- **A tap on a bulb** turns that room's light on or off (`set_light` with `room` and `is_on`). Brightness stays voice only.
- **Cooler and Warmer** move the setpoint by 0.5 °C, between 16 and 30 °C (`set_thermostat` with `target_c`). The thermostat server has no mode setter, so the mode stays display only.
- **The same gate as the music buttons.** `SCREEN_TOOLS` in `harness/tool_policy.py` now maps each screen tool to the only arguments a tap can send: no arguments for music, a room name and a true or false for a light, and a number from 16 to 30 in half degrees for the thermostat. Anything else, including an extra field such as `brightness`, is refused as `denied_screen`. The rule must still be `auto` with no undo window and no guard, and the server checks the arguments again, as for a spoken request.
- **Greyed out when they would be refused.** The bot sends `policy.screen_tools()` in the state, so a document agent, which starts no light, thermostat, or music server, shows those controls disabled.
- **No new motion.** The bulbs are buttons in the existing grid, the steps are two buttons under the dial, and a section is still redrawn only when its data changes. Rapid taps on Warmer add up on the page and send one setpoint each.
- **The whole robot takes the agent's colour** (ADR-039): the shell, ear, and highlights are mixed from the agent's colour with `color-mix`, and the visor stays dark so the eyes still stand out.

The LLM is not told about a tap. If the user then asks about a light, the model calls `list_lights`.

## Sources

- ADR-027 (robot face), ADR-031 (Spotify player), ADR-011 and ADR-024 (tool policy).
- aiohttp `web.AppRunner` shutdown timeout, default 60 s: installed `aiohttp/web_runner.py`.
