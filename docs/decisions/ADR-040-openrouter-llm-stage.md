# ADR-040: OpenRouter as an LLM stage, with a curated and priced model list

Date: Nov 4, 2026. Status: built, and smoke-tested live on six models on Nov 4, 2026 (results below).

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
- **The generalist defaults to Anthropic directly.** That avoids the fee and one routing hop on the main path. Since the Nov 4 update it can run on a listed OpenRouter model for one session when the user picks one.

## Alternatives

- **Any model ID typed in the builder:** untested models may be slow, may call tools badly, and would have no price, so the cap could not count them.
- **Fetch prices from the models endpoint at start:** an outside call on every start, and a price that changes under a run. Prices live in the config and are rechecked before a model goes live, like the others.
- **One direct integration per provider:** more keys, more SDKs, and more caps for the same result.

## Consequences

- **Live use needs credits and approval.** Recommended: $10, which also lifts the free-model limit from 50 to 1,000 requests a day.
- **Before a model goes live:** a small smoke test per model, a tool call and a short answer, at about $0.01 in total, then `bench.latency_probe`. Slow models leave the list.
- **Usage metrics.** OpenRouter reports usage through the OpenAI streaming format, which Pipecat turns into `LLMUsageMetricsData`. Checked live on Nov 4: the ledger recorded $0.0095 for the six-model smoke test from those reports.

## Live smoke test, Nov 4, 2026

$20 of credits were loaded. One run per model as the Customers agent (ADR-039), text input and fake TTS, two turns: "Who is the account manager for Mayon Builders?" (answer: Lea Manalo) and "What is the capital of France?" (should be refused). Total $0.0095 by the ledger.

| Model | Lookup | Refusal | Cost | Run time |
|---|---|---|---|---|
| `anthropic/claude-haiku-5.5` | Right | Right | $0.0022 | 10.4 s |
| `openai/gpt-oss-120b` | Said it was not in the documents | Right | $0.0020 | 9.3 s |
| `openai/gpt-oss-20b` | No reply in 45 s, five searches | Not reached | $0.0006 | 46.0 s |
| `qwen/qwen3.8-flash` | Right | Right | $0.0012 | 23.5 s |
| `deepseek/deepseek-v4.1-flash` | Right | Right | $0.0005 | 6.8 s |
| `google/gemini-3.1-flash-lite` | Said it was not in the documents | Right | $0.0031 | 11.6 s |

The misses were a bug in the docs server, not the models. Every search found the right chunk, but each result was cut to its first 500 characters, and the Mayon Builders row sat about 900 characters into a chunk of CSV rows. Snippets are now built from the lines that match the query (`snippet` in `servers/tasks/docs/server.py`).

The three that missed were run again after the fix, for $0.0017 in total:

| Model | Lookup | Refusal | Searches | Cost | Run time |
|---|---|---|---|---|---|
| `openai/gpt-oss-120b` | Right | Right | 1 | $0.0006 | 7.8 s |
| `google/gemini-3.1-flash-lite` | Right | Right | 1 | $0.0009 | 8.5 s |
| `openai/gpt-oss-20b` | Right | Right | 2 | $0.0002 | 14.3 s |

So all six answer and refuse correctly on this case. Two style slips: GPT-OSS 20B put the file path in parentheses, and GPT-OSS 120B named the file rather than its title, though the prompt asks for neither. TTS would read both aloud. Sonnet 5.5 was not run, to keep within the approved spend. One question per model is a smoke test, not a comparison. Run times are whole sessions, including MCP server start, not reply latency.

## Update, Nov 4, 2026: the model picked per session

Quiel asked to choose the model when picking an agent, at the start of each session.

- **The picker** shows a model list under each agent: the agent's own model first, then `direct` and every listed OpenRouter model. Picking a model starts a session with that agent and model. Picking the same agent with another model also restarts the session.
- **Stored per agent** in `data/agents/active.json` as `{"id": ..., "models": {agent: model}}`. Picking the agent's own model clears its entry. A model not in the list is refused, and one in a hand-edited file is ignored.
- **The generalist too.** Its default stays Anthropic direct. With another model picked, the picker warns that other models may handle its many tools less well; only Haiku 5.5 has been run live with all its tools.
- **Scripted runs** take `--model` with `--agent`, so a test can try a model without the face.
- **No spend unasked.** A fake LLM stage stays fake whatever is picked, and every model in the list is priced, so the cap counts it.

## Sources

- https://openrouter.ai/api/v1/models (prices, `supported_parameters` includes `tools`), Nov 4, 2026.
- https://openrouter.ai/docs/faq (free-model limits, credit expiry). The fee percentage was not shown on the page. It is reported at https://usagebox.com/articles/openrouter-pricing-2026.
- Pipecat 1.13.0, installed: `pipecat/services/openrouter/llm.py`.
