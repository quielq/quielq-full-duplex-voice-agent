# ADR-040: OpenRouter as an LLM stage, with a curated and priced model list

Date: Nov 4, 2026. Status: built and tested on fakes only. No OpenRouter call has been made: the account has no credits yet.

## Context

Quiel wants each agent to have its own model, chosen by the user (ADR-039). Anthropic and Groq are wired in directly, one model each. OpenRouter gives one key and one API for many models, and `OPENROUTER_API_KEY` already had a $5 daily cap approved on Nov 1, 2026, for the model comparison.

Two checks on Nov 4, 2026:

- `GET https://openrouter.ai/api/v1/key` showed the key is valid, on the free tier, with no usage.
- `.env` had a second, blank `OPENROUTER_API_KEY` line, so python-dotenv read the key as empty. The blank line was removed, and the loader now reads it as set.

## Decision

- **`llm: openrouter`** builds Pipecat 1.13's `OpenRouterLLMService` (`settings=OpenRouterLLMService.Settings(model=..., max_completion_tokens=8192, system_instruction=...)`). No new dependency.
- **The model comes from `providers.openrouter.model`,** or from the agent on a live run (ADR-039).
- **`providers.openrouter.models` is the curated list the builder offers.** Each model supports tool calls, and the list is short so voice latency stays predictable. Prices are from https://openrouter.ai/api/v1/models on Nov 4, 2026, per million tokens, input then output:

| Model | Input | Output |
|---|---|---|
| `anthropic/claude-haiku-5.5` | $0.10 | $0.50 |
| `openai/gpt-oss-120b` | $0.15 | $0.60 |
| `openai/gpt-oss-20b` | $0.03 | $0.13 |
| `qwen/qwen3.8-flash` | $0.15 | $0.47 |
| `deepseek/deepseek-v4.1-flash` | $0.045 | $0.50 |
| `google/gemini-3.1-flash-lite` | $0.25 | $1.50 |
| `anthropic/claude-sonnet-5.5` | $2.00 | $10.00 |

- **Every call goes through the cost ledger** under the existing `openrouter` cap, priced from `prices.openrouter.by_model`.
  - There is no default rate: a model without a price is refused at build time, so the cap can never count a model as free.
  - A test checks that every listed model has a price.
- **Credit fee not counted.** OpenRouter charges a fee when credits are bought, reported as 5.5% with a $0.80 minimum on card. The ledger counts list price only, so the cap is slightly generous by that fee.
- **Network watch.** `openrouter.ai` joins the hosts that `NetworkWatch` probes on an error (ADR-037).
- **The generalist stays on Anthropic directly.** That avoids the fee and one routing hop on the main path.

## Alternatives

- **Any model ID typed in the builder:** untested models may be slow, may call tools badly, and would have no price, so the cap could not count them.
- **Fetch prices from the models endpoint at start:** an outside call on every start, and a price that changes under a run. Prices live in the config and are rechecked before a model goes live, like the others.
- **One direct integration per provider:** more keys, more SDKs, and more caps for the same result.

## Consequences

- **Live use needs credits and approval.** Recommended: $10, which also lifts the free-model limit from 50 to 1,000 requests a day.
- **Before a model goes live:** a small smoke test per model, a tool call and a short answer, at about $0.01 in total, then `bench.latency_probe`. Slow models leave the list.
- **Usage metrics.** OpenRouter reports usage through the OpenAI streaming format, which Pipecat turns into `LLMUsageMetricsData`. Not yet checked against a live response.

## Sources

- https://openrouter.ai/api/v1/models (prices, `supported_parameters` includes `tools`), Nov 4, 2026.
- https://openrouter.ai/docs/faq (free-model limits, credit expiry). The fee percentage was not shown on the page. It is reported at https://usagebox.com/articles/openrouter-pricing-2026.
- Pipecat 1.13.0, installed: `pipecat/services/openrouter/llm.py`.
