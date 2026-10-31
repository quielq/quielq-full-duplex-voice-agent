# ADR-016: No-wake-word filters, built and off by default

Date: Oct 31, 2026. Status: built on fakes and synthetic audio. Not yet run with a live LLM, Flux, or a real room.

## Context

`docs/architecture.md` calls for three filters so the agent can run without a wake word, plus a mute switch and a `require_wake_after_idle` fallback. The replan gives `stay_silent` and ACTIVE/IDLE Nov 3. Quiel asked for everything buildable to be built without spending.

## Decision

`harness/addressing.py`, all behind `addressing.enabled` (false in both profiles):

* **Level gate (`LevelGate`, `MicGate`).** First processor after the mic, off by a second flag, `addressing.gate.enabled`. The floor comes from the first 500 ms, then follows quiet blocks. 400 ms of pre-roll is released when the gate opens. It closes only in IDLE, so barge-in and ACTIVE dialogue are never blocked.
* **Conversation state (`ConversationState`, `AddressingFilter`).** ACTIVE for `idle_after_s` (30 s, a hypothesis) after the agent spoke or was spoken to. In IDLE a final transcript must look like a question, a polite request, or a command verb, or carry a name. Anything else is dropped before the LLM, the way `DuckingProcessor` drops a backchannel.
* **`stay_silent`.** A harness tool, not an MCP tool: it touches no state, so it has no hints or policy, and it has to return `run_llm=False` so that no second call or TTS happens. Offered only when addressing is on.
* **Order.** `AddressingFilter` runs after `PolicyGate`, so "cancel" still reaches a held action in IDLE.
* **Mute.** `MicGate` drops all audio when muted, regardless of the other flags. `kill -USR1 <pid>` toggles it in mic mode and the log prints `MIC MUTED`. A Pi button can send the same signal. There is no voice unmute, because a muted mic cannot hear it.

## Differences from the review

* The review places the gate on the Pi only. The code is the same on both profiles, since profiles may differ only in devices and timing.
* The gate's `reseat_ms` (15 s) is new: a test showed that a steady noise above the margin would otherwise hold the gate open forever.

## Alternatives

* **`stay_silent` as an MCP server tool:** adds a process and a hop to do nothing, and the policy wrapper would have to learn about `run_llm=False`.
* **Pre-screen with a cheaper model in IDLE:** adds a round trip; left for later if the main model's cost matters.
* **A small on-device addressee classifier:** more accurate than word rules, but no training data exists yet.

## Consequences

* The IDLE word rules will drop real statements such as "I'm cold", which the design accepts for IDLE. They need tuning on recorded rooms.
* The next live task run should cover `addressing.yaml`, since `gpt-oss-20b` may overuse or ignore `stay_silent`.
* **Flux behavior with gaps in the audio stream is unverified.** If Flux needs continuous audio to hold its end-of-turn state or its socket, the gate needs silent keep-alive frames. Check this before turning the gate on with Flux. `close_ms` is 1.5 s so that trailing silence still reaches STT.
* The speaker-echo level during playback does not matter to the gate, because playback keeps it open.

## Sources

* `docs/architecture.md`, "No wake word: deciding when it is being addressed"
* Installed source: `pipecat/frames/frames.py` (`FunctionCallResultProperties.run_llm`), `pipecat/adapters/schemas/function_schema.py`
