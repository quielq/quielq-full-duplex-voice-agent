# ADR-003: Pipecat 1.13 API choices that differ from the docs

Date: Oct 21, 2026. Status: accepted.

## Context

The review and prompt were written against Pipecat documentation and a third-party analysis of 1.12. Reading the installed 1.13.0 source turned up several deprecations that the docs do not mention.

## Decision

| Docs say | 1.13.0 reality | We use |
| --- | --- | --- |
| `MCPClient.register_tools(llm)` | Deprecated since 1.8.0, removal in 2.0.0 | `LLMContext(tools=await client.tools())`; handlers register automatically and the connection closes with the pipeline |
| `PipelineTask`, `PipelineRunner` | Deprecated since 1.3.0 | `PipelineWorker`, `WorkerRunner` with `add_workers()` then `run()` |
| VAD on `TransportParams(vad_analyzer=...)` | Removed | A standalone `VADProcessor` right after the input, so VAD frames reach the STT and, later, the DuckingProcessor. The user aggregator's own `vad_analyzer` param is left unset to avoid running Silero twice |
| Service `model=` / `voice=` constructor args | Deprecated since 0.0.105 | `settings=Service.Settings(model=..., voice=...)` |
| Interruptions | `user_turn_strategies` (as the review says) | `UserTurnStrategies(start=[VADUserTurnStartStrategy(), TranscriptionUserTurnStartStrategy()], stop=[TurnAnalyzerUserTurnStopStrategy(LocalSmartTurnAnalyzerV3())])`, set explicitly even though it equals the default |

Also confirmed: `TextAggregationMode.SENTENCE` exists and is passed explicitly to every TTS service. `MinWordsUserTurnStartStrategy` and a `WakePhraseUserTurnStartStrategy` both exist; the second is a candidate for the `require_wake_after_idle` fallback.

MCP tool results reach the LLM context as a JSON-encoded string of the server's JSON text. Real LLMs read this fine; our fake LLM decodes it twice.

## Alternatives

`register_tools(llm)` still works and is the Gate 1 wording, but it would add a deprecation to remove before 2.0, so the supported path is used. Gate 1's intent (MCPClient, both transports, a spoken request) is unchanged.

## Consequences

Code built from pre-1.8 examples will not match ours. Recheck this table on every Pipecat upgrade.

## Sources

* Installed source: `pipecat/services/mcp_service.py` (`register_tools` docstring), `pipecat/pipeline/task.py`, `pipecat/pipeline/runner.py`, `pipecat/processors/aggregators/llm_response_universal.py`, `pipecat/turns/user_turn_strategies.py`
* https://reference-server.pipecat.ai/en/latest/api/pipecat.services.mcp_service.html
