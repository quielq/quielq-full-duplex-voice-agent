# ADR-015: Aura-2 delivery falls below real time, try Cartesia live

Date: Oct 31, 2026. Status: built and off by default. Cartesia has not yet run in the live bot.

## Context

Bot speech was choppy on MacBook speakers and, later, on earphones too. One earphone run (`20261031-162512-c34da9`) had sounded smooth, which pointed at the speakers. Hop timing from commit `7d6aa4f` ruled that out:

| Run | Output | Aura-2 arrival while speaking | Longest speaker write |
|---|---|---|---|
| `20261031-163553-f4c9dd` | Speakers | 0.43x | 51 ms |
| `20261031-163839-e30fe8` | Earphones | 0.54x | 54 ms |

Writes kept up with arrival in both runs, so the output was short of audio, not blocked. The smooth earphone run was probably a good moment, but it predates hop timing, so this cannot be confirmed.

A direct test with no pipeline (`run_id` `delivery-compare`) alternated the same 174-character text at 16 kHz:

| Round | Aura-2 speed | Aura-2 starved | Aura-2 largest gap | Cartesia speed | Cartesia starved | Cartesia largest gap |
|---|---|---|---|---|---|---|
| 1 | 2.27x | 0 s | 0.06 s | 5.26x | 0 s | 0.09 s |
| 2 | 2.25x | 0 s | 0.06 s | 7.97x | 0 s | 0.09 s |
| 3 | 0.64x | 5.57 s | 2.20 s | 7.33x | 0 s | 0.09 s |
| 4 | 0.86x | 2.22 s | 1.07 s | 8.25x | 0 s | 0.09 s |

First audio took 0.27 to 0.34 s on Aura-2 and 0.15 to 0.20 s on Cartesia.

The Mac and the Wi-Fi link carried Cartesia at full speed between the slow Aura-2 rounds, so they are not the general limit. This does not separate Deepgram's service from the network route to it. Cartesia's route is not known and may differ.

## Later evidence on the route

`bench.doctor` times TLS handshakes with no key and no spend. Five samples each, Oct 31, 2026, about 16:53:

| Host | Median | Worst |
|---|---|---|
| `api.deepgram.com` | 542 ms | 567 ms |
| `api.groq.com` | 115 ms | 142 ms |
| `api.cartesia.ai` | 26 ms | 40 ms |

An earlier single sample to Deepgram took 1555 ms. This shows that Cartesia terminates far closer to this network than Deepgram does, which leans toward the route over Deepgram's service. It is one time of day, and a handshake says nothing about sustained throughput, so it does not settle the question.

## Decision

`deepgram_aura2` stays the configured choice until a live Cartesia run is judged by ear. The `cartesia` stage now reports hop timing through a shared `HopTimedTTS` mixin and requests 24 kHz, matching the output. Cartesia has a $0.10 daily cap and the public Skylar voice.

Cartesia does not use the fresh socket workaround from ADR-012. Its audio carries a context ID and Pipecat sends a cancel for that context on interruption.

If the live run is smooth, `CLAUDE.md` needs changing, because it names Aura-2 for bulk development. A later ADR will record that.

## Alternatives

* **Larger playback buffer:** at 0.43x a 6 s reply needs about 3.4 s of buffer before it starts.
* **Aura-2 over HTTP:** tested earlier at 0.32x to 0.50x, with first audio at 0.94 to 2.15 s.
* **Phone hotspot test:** would separate Deepgram from the route, but would not give a working alternative.

## Consequences

* Cartesia is priced higher per character in `configs/providers.yaml`: $0.00005 against $0.00003. That price was not rechecked for this ADR.
* Cartesia sends audio in fast bursts, so `backlog_ms` in `audio_hop` will be large during a reply. This is expected.
* Barge-in stale audio is covered by a fake socket test only. Live interruption needs checking.
* ADR-014's 16 kHz setting for Aura-2 has no proven benefit and is unchanged.

## Sources

* Installed source: `pipecat/services/cartesia/tts.py`
