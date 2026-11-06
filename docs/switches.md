# Switches: what can be turned on and off

Every part of ME3 that can be switched, where to switch it, its default, and whether it has run live. Defaults are the same in `configs/mac.yaml` and `configs/pi.yaml` unless a row says otherwise. A command-line flag or environment variable changes one run only; editing the YAML changes the default.

"Live" means it has run with real providers at least once. "Fakes" means it has only run in tests and scripted runs with no spend.

## Providers (stages)

Each stage defaults to a fake that costs nothing. A real provider runs only when a stage selects it, its key is in `.env`, and its daily cap in `configs/providers.yaml` is above zero (`bench/cost_ledger.py`).

| Stage | Choices | Default | Switch | Notes |
|---|---|---|---|---|
| `stt` | `fake`, `deepgram_flux`, `voice_listen` | `fake` | `--stages stt=...` | `voice_listen` is Flux through our MCP server (ADR-032). Live |
| `llm` | `fake`, `groq`, `anthropic`, `openrouter` | `fake` | `--stages llm=...`, `--model` | Haiku 5.5 on `anthropic` is the live LLM (ADR-025). Groq stays wired, limited by its free tier (ADR-017). OpenRouter models must be priced (ADR-040) |
| `tts` | `fake`, `fake_words`, `fake_words_fast`, `deepgram_aura2`, `cartesia`, `voice_speak` | `fake` | `--stages tts=...` | Cartesia (or `voice_speak`, Cartesia through our MCP server) has word timestamps, so context keeps only heard words. Aura-2 cannot (ADR-006, ADR-015) |
| `music` | `fake`, `spotify` | `fake` | `--stages music=spotify` | Demo only, plays in the Spotify app (ADR-022) |

The live command is in `CLAUDE.md`. `ME3_STAGES` does the same as `--stages` for the class-benchmark entry (`bench/fdb_adapter.py`).

## Tool backends

Set in `tool_stages` in `configs/providers.yaml`, or per run with `ME3_TOOL_STAGES=name=choice,...`.

| Backend | Choices | Default | Notes |
|---|---|---|---|
| `search` | `fake`, `ddgs`, `tavily` | `fake` | ADR-021 |
| `weather` | `fake`, `open_meteo` | `open_meteo` | Real by default, free (ADR-024) |
| `fetch` | `fake`, `http` | `http` | Real by default (ADR-024) |
| `comms` | `mock`, `bridge` | `mock` | `bridge` sends real messages through `scripts/mac_phone_bridge.py` (ADR-024) |
| `delegate` | `fake`, `anthropic` | `fake` | Background worker on Haiku 5.5 (ADR-035). One live task, Nov 4 |

`ME3_WORKSPACE_LIVE=1` makes `bash`, `serve` and `open_in_browser` real. They still pass the command policy and run in the OS sandbox, and refuse with no sandbox (ADR-026, ADR-034). Without it they are mocks.

## Voice pipeline

| Component | Where | Default | Switch per run | Status |
|---|---|---|---|---|
| End of turn | `turn.stop` | `auto` (Flux's own end of turn with Flux, Smart Turn otherwise) | `--turn-stop` | `flux_forced` is built and failed its rule (ADR-019). Live |
| Flux eager end of turn | `providers.deepgram.flux.eager_eot_threshold` | on at 0.5 | `--flux eager_eot_threshold=off` | Saves 159 ms on chat, 355 ms on tool turns (ADR-046). Live |
| Other Flux settings | `providers.deepgram.flux` | Deepgram's defaults | `--flux name=value` | ADR-018 |
| Barge-in | `barge_in.mode` | `immediate` | `--barge-in duck` | Duck-then-decide is a prototype; it stopped 0.2 s later on the class benchmark (ADR-007, ADR-045) |
| Echo cancelling | `audio.echo_cancel` | off on the Mac, on on the Pi | `--echo-cancel` | WebRTC AEC (ADR-013). Live mic runs refuse built-in speakers without it |
| Self-echo filter | `self_echo.enabled` | on (acts only with echo cancelling) | profile | Drops the bot's own words that cut it off (ADR-042) |
| Waiting phrase | `filler.enabled` | on | profile | Five stored clips after 1.5 s, or when a slow tool starts (ADR-033). Live |
| Addressing (no wake word) | `addressing.enabled` | off | profile, `ME3_ADDRESSING=1` for the class benchmark | Fakes only; did nothing on the class benchmark (ADR-016, ADR-045) |
| Addressing level gate | `addressing.gate.enabled` | off | profile | Synthetic audio only |
| Mic mute | runtime | unmuted | `kill -USR1 <pid>`, or the face's mute button | Live |
| Network-loss recovery | `network.enabled` | on | profile | TCP probe, stored clips (ADR-037). Live once on the Mac |
| Context window | `context.keep_turns` | 20 | profile (0 keeps all) | Live on the Pi class benchmark, 26 trims in 41 turns (ADR-047) |
| Prompt caching | `providers.anthropic.prompt_caching` | on | `ME3_PROMPT_CACHE=0` for the class benchmark | About 87% less LLM cost (ADR-045) |
| Reply templates | `reply_templates.enabled` in `configs/tool_policy.yaml` | off | `--reply-templates` | Skips the second LLM call on simple tools, 296 ms saved, under its bar (ADR-020) |

## Screen

| Component | Where | Default | Switch per run | Status |
|---|---|---|---|---|
| Robot face | `ui.enabled` | off | `--ui` | 127.0.0.1 only (ADR-027, ADR-038). Live |
| Spotify in the page | `ui.spotify_player` | off | `--spotify-player` | Needs Premium (ADR-031) |
| Agent | `configs/agents.yaml`, `data/agents` | generalist (VIA: Generalist) | `--agent`, the picker | Document agents see four read tools only (ADR-039) |

## Never switchable

These hold whatever the other switches say, and no flag turns them off:

- The daily spending cap per provider. A cap of zero refuses every call (`bench/cost_ledger.py`).
- The tool policy: undo windows, read-backs, and spoken confirmation (`harness/tool_policy.py`, ADR-011, ADR-024).
- The command policy and OS sandbox for `bash` and `serve` (ADR-034).
- The screen's argument checks for taps (`ToolPolicy.run_from_screen`, ADR-038).
- The document agents' folder scope, enforced in the docs server (ADR-039).
