# ADR-014: Request Aura-2 at 16 kHz

Date: Oct 31, 2026. Status: implemented on Quiel's go. Not yet heard in a live run.

## Context

Live speaker runs sounded very choppy: speech played at about 42% of real time, with output gaps that clustered at multiples of about 260 ms. Those gaps match the 279 ms round trip from Quiel's network in the Philippines to `api.deepgram.com` (Sacramento). Bandwidth is not the limit: 115 Mbps up, 243 Mbps down.

A direct websocket test, with no Pipecat and no speakers, sent the same 174-character text once at each rate:

| Sample rate | Audio | Delivery time | Speed | Gaps over 200 ms |
|---|---|---|---|---|
| 24 kHz | 9.92 s | 22.84 s | 0.43x | 28 |
| 16 kHz | 10.92 s | 4.85 s | 2.25x | 0 |

This is one sample of each. The 24 kHz stream was intermittently fine in live runs, and data falls by only a third while speed rose fivefold, so bit rate alone may not explain the gap. TCP congestion on the long path is the likely mechanism, but this was inferred from timing and not captured directly.

This corrects ADR-013, which suspected the speaker write. Earlier probes played normally because their audio was local.

## Decision

`deepgram.tts_sample_rate: 16000` in `configs/providers.yaml`. Pipecat's output resamples to the transport rate. Billing is per character, so cost does not change.

## Alternatives

* **Playback buffer:** smooths gaps but delays every reply, and does not help when delivery stays below real time.
* **Compressed encodings:** less data, but Pipecat's service treats the stream as raw audio, so this would need a decoder.
* **A closer provider:** may be needed, not tested.

## Consequences

* Speech is slightly less crisp, with less high-frequency detail.
* The same network will feed the Pi, so a demo there depends on this too.
* Cartesia was not checked from this network. Truncation tests and final runs rely on it, so measure its delivery first.
* Latency and echo numbers from runs before this change were taken with broken playback, so do not reuse them.

## Sources

* https://developers.deepgram.com/docs/tts-websocket-streaming
* Test script kept outside the repo. Ledger entries: `run_id` `delivery-test`.
