# ADR-037: Say once that the network is gone, then wait for it

Date: Nov 4, 2026. Status: built and tested on fakes only. No paid call. The two clips are not made yet.

## Context

The demo runs on a Raspberry Pi over home or campus Wi-Fi. ASR (Deepgram Flux), TTS (Cartesia), and the LLM (Haiku) are cloud APIs, so a dropped network used to go silent: the user spoke, nothing came back, and nothing said why. The LLM error phrase (`harness/recovery.py`) did not help, because it is spoken through TTS, which is also down. The phrase cache (`harness/phrase_cache.py`) already listed a `network_lost` phrase, and the face already had a sticky `voice_down` state, but nothing detected the loss.

What the harness already sees when a provider fails:

- Pipecat `ErrorFrame`s from the STT, LLM, and TTS services. A failed WebSocket connect in the Flux or Cartesia service is reported this way (`pipecat/services/websocket_service.py`, category `CONNECTIVITY`), and so is a failed connect through the `voice_listen` and `voice_speak` relays, because `RelayClient.open` raises inside the service's own `_websocket_connect`.
- The `tts_failed` and `pipeline_error` events those frames already produce (`harness/events.py`).

An error alone does not say whether the network is down or one request was bad (a schema rejection, a 400, a bad voice id).

## Decision

- **`harness/network.py` adds `NetworkWatch`,** an observer on the worker. Any `ErrorFrame` from an STT, LLM, or TTS service starts a check of that stage.
- **The check is a plain TCP connect** to the stage's provider host (`api.deepgram.com`, `api.cartesia.ai`, `api.anthropic.com`, `api.groq.com`, port 443) with a 1.5 s timeout, two tries 0.2 s apart. DNS failing counts as down. It opens no TLS session and makes no API call, so it is free and cannot touch a cap. Relay stages probe the provider their voice server talks to, which shares this machine's network. Fake stages have no host, so a fake's error is never an outage.
- **Host answers:** one bad request. It logs `provider_error` with `reachable: true`, and the normal LLM error phrase still plays.
- **Host does not answer:** an outage. It logs `network_lost`, plays the stored `network_lost` clip ("I've lost my connection. Give me a moment.") once, and holds back every other output until the network is back: the waiting phrase, a late reply, the LLM error phrase. Errors that arrive at the same moment share one probe; errors during the outage from any stage are added to it quietly.
- **`ClipPlayer`** plays stored clips as plain output audio, just before the output transport, and drops other output audio while the watch is down. Its clip has no text, so it never reaches the LLM context or the `played_up_to` word count, and an interruption flushes it from the output queue like any other output audio. Its bot start is logged as `phrase_audio_out`, never `first_audio_out`, so it does not count toward latency.
- **The mic keeps listening.** VAD, Smart Turn, the mute, and the face are all local and keep running.
- **A request the LLM could not answer is dropped from the context** (`lost_turn_dropped`). The user was told the connection was lost, so the request should not be acted on minutes later when the next turn arrives. They can say it again.
- **Recovery:** the failed hosts are probed again after 1, 2, 4, then every 8 s. When all answer, it logs `network_restored` with the outage length and the number of probes, and the next turn runs as usual. `NetworkWatch.on_restored` is a list of hooks for anything that should act then.
- **The face** shows "Connection lost" and the sad face from `network_lost` until `network_restored`. This is a separate `network_down` flag, because the clip's own audio must not clear it the way reply audio clears `voice_down`.
- **The spending cap notice is now the stored `cap_reached` clip** ("I've reached today's spending limit, so I have to stop here."), played the same way. It needs no TTS call, so it plays whichever provider hit its cap, where the old spoken notice had to stay quiet when TTS was the capped provider. `recovery.cap_phrase` is replaced by `recovery.cap_clip` and `recovery.network_clip`.
- **The clips are not made yet.** A missing clip plays the 300 ms placeholder tone and logs `phrase_clip` with `real: 0`. One approved command makes both, about 100 characters, about $0.005: `ME3_LIVE=1 uv run python -m bench.make_phrases --recovery`.
- Settings are under `network:` in `mac.yaml` and `pi.yaml`: `enabled`, `probe_timeout_s`, `probe_attempts`, `backoff_s`.

## Alternatives

- **Treat every provider error as an outage:** a schema rejection would then say "I've lost my connection", which is wrong and confusing. The probe is what tells the two apart.
- **Probe with a real API call, such as listing models:** it needs a key, can fail for reasons that are not the network, and may count against a quota. A TCP connect answers the only question asked.
- **Probe all the time, on a timer:** it would catch an outage before the user speaks, but it costs a connection every few seconds on the Pi for something that only matters when a turn fails. Probing starts on the first error and stops on recovery.
- **ICMP ping:** needs privileges on the Pi and says nothing about port 443 being open, which is what matters behind campus firewalls.
- **Say the phrase through TTS:** TTS is one of the things that is down.
- **Keep the lost request in the context:** a real LLM sees two user messages in a row after recovery and does both, so a timer or message the user thought was lost happens late. The fake LLM showed exactly this in the test.

## Consequences

- New events: `provider_error`, `network_lost`, `network_restored`, `lost_turn_dropped`, `phrase_played`, `phrase_clip`, `phrase_audio_out`. `bench/run_report.py` does not flag them yet.
- Pipecat's WebSocket services give up after three reconnect attempts with a 4 to 10 s backoff and then mark themselves unusable. An outage longer than about 30 s can leave Flux or Cartesia unusable after the network is back. The LLM, on HTTP, recovers by itself. Reconnecting a stream after a drop is the voice-reconnect work; it can hook into `NetworkWatch.on_restored`.
- In mic mode with the STT down, no transcript arrives, so the outage is usually found by the STT's own connect error, not by a turn.
- Only tested on fakes: a fake LLM that fails to connect and a probe that reports a fake network, then both come back. The real TCP probe is tested only against a port on this machine. A live test means turning off the Wi-Fi during a run, after the clips are made.

## Sources

- Pipecat 1.13.0, installed: `pipecat/services/websocket_service.py` (`_try_reconnect`, three attempts, backoff 4 to 10 s, then `force_treat_as_permanent`), `pipecat/utils/errors.py` (`ErrorCategory.CONNECTIVITY` is not permanent), `pipecat/pipeline/worker.py` (`ProcessorUnusablePolicy.CONTINUE` is the default), `pipecat/pipeline/worker_observer.py` (each observer has its own queue, so a probe does not block the pipeline).
- ADR-009 and ADR-033 (waiting phrase, the same local-clip approach), ADR-027 (robot face), ADR-032 (voice servers).
