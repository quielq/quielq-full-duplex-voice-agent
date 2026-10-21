# ADR-008: Voice events ride long-running MCP calls as logging notifications

Date: Oct 21, 2026. Status: accepted for the stub servers; revisit with the real voice servers.

## Context

The voice servers must push events (`speech_started`, `transcript_partial`, `played_up_to`, ...) to the harness. The review plans a `VoiceMCPBridge` on the raw MCP SDK `ClientSession` with a message handler, because Pipecat's `MCPClient` exposes no notifications. Checking MCP SDK 2.3.0 and FastMCP 4.1.3 turned up two constraints the review did not mention:

1. `ServerNotification` is a closed union (cancelled, progress, logging message, resource and list changes, elicitation, subscriptions). There is no typed custom notification. Custom methods need the SDK's client-extension mechanism (`NotificationBinding`), which also needs a matching server side.
2. FastMCP's `Context.send_notification` notes that on a sessionless (2026-07-28 protocol) connection there is no standing server-to-client channel, so **a notification not related to an in-flight request is dropped**. Only the open request's stream carries it.

## Decision

* Voice events are **MCP logging notifications** on the `voice` logger, with the event in structured data (`servers/voice_events.py`). Every client already receives these through `ClientSession(logging_callback=...)`.
* Events are sent **during a long-running tool call**: `speak` stays open while the utterance plays and returns when it ends or is stopped; `listen` stays open for one utterance. `stop`, `duck`, `unduck`, and `stop_listening` are separate calls made concurrently on the same session.
* This renames the review's `start_listening` to `listen` (a call that lasts as long as the listening does). The rest of the review's tool names stay.
* Each event carries `t_emit_ns` from `time.monotonic_ns()`, a single system-wide clock on macOS and Linux, so the bridge measures the local hop directly.
* The bridge warms each session with one harmless call at connect.

## Results (stub servers on the Mac, loopback, 5 runs per transport)

| Measure | stdio | streamable HTTP |
| --- | --- | --- |
| Event hop, server emit to bridge, p50 | 2.3 to 4.0 ms | 2.1 to 4.2 ms |
| Event hop p95 | 4.8 to 5.8 ms | 5.0 to 10.4 ms |
| First control call on a fresh session | 37 to 75 ms | 6 to 7 ms |
| Control call (`duck`, `stop`) after warm-up | 5 to 12 ms | 6 to 11 ms |
| `stop` sent to `playback_stopped` received | 2.5 to 8.9 ms | 4.3 to 8.5 ms |

The extra local MCP hop costs single-digit milliseconds, well under the review's "more than a few tens of milliseconds" threshold for falling back to thin Pipecat services. These numbers are for control and events only; audio still needs its side channel (Unix socket or shared memory), which is not built yet. Pi numbers will be higher and must be re-measured.

## Alternatives

* Custom notification methods through `ClientExtension` / `NotificationBinding`. Cleaner names, but more moving parts on both sides and less tooling support. Revisit if logging volume becomes noisy.
* Progress notifications on the open call. Needs a progress token per call and carries only numbers plus a message string.
* Polling a `status` tool. Rejected: adds latency equal to the poll interval.

## Consequences

* A voice server must always have a call open to report anything. `listen` has to be re-issued after each utterance (or made to cover a whole session).
* FastMCP HTTP sessions expire after 30 minutes idle (review), so the bridge needs reconnect logic before long runs.
* Pipeline wiring is still a prototype: events arrive as `VoiceEventFrame`s, and nothing in the pipeline acts on them yet.

## Sources

* Installed source: `mcp/types` (`ServerNotification`), `mcp/client/extension.py` (`NotificationBinding`), `fastmcp/server/context.py` (`send_notification`, sessionless note)
* `bench/voice_bridge_probe.py`, `tests/test_voice_bridge.py`
