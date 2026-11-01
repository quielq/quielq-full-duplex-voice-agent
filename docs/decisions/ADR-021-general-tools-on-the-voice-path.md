# ADR-021: General tools on the voice path

Date: Nov 1, 2026. Status: approved by Quiel, Nov 1, 2026. Departs from the review.

## Context

Quiel asked for eleven tools: search, web search, search document, index documents, bash, grep, read, write, edit and send files, serve, fetch content, get weather, and local search. That is OnIt's default tool set without `github_repo`.

The review gives `knowledge` only `web_search` and `fetch_page`, and leaves shell and file work to a background worker behind `delegate`. This decision gives those tools to the voice LLM directly.

## Decision

Three MCP servers, written in this repo and using OnIt's tool names and meanings:

| Server | Tools |
| --- | --- |
| `knowledge` | `search`, `fetch_content`, `get_weather` |
| `docs` | `index_documents`, `search_document`, `local_search` |
| `workspace` | `bash`, `grep`, `list_files`, `read_file`, `write_file`, `edit_file`, `send_file`, `serve`, `serve_status` |

Search and web search are one tool with a `type` of web or news, as in OnIt. `serve` is split in two because policy is per tool, so checking status needs no confirmation.

Each of these limits holds in the server or the harness, not in the prompt:

- Files: writes stay in `$ME3_DATA_DIR/workspace`. Reads may also use `$ME3_DATA_DIR/documents`. Paths are resolved first, so a symlink cannot leave the roots.
- `bash` and `serve` are mocks unless `ME3_WORKSPACE_LIVE=1`. Live, they get spoken confirmation and then the undo window, run with the workspace as working directory and home, and receive a short allowlist of environment variables.
- `send_file` is always a mock. It copies into `$ME3_DATA_DIR/send_outbox` and has no destination argument, so it cannot send anything out.
- `fetch_content` refuses loopback, private, and link-local addresses, on every redirect.
- `bash` stops at the policy timeout. Longer work goes through `serve`, so the voice loop never blocks.

Tool backends are selected under `tool_stages` in `configs/providers.yaml` and default to fakes. Search and weather call the cost ledger from the server process, and `ddgs`, `open_meteo`, and `tavily` have caps of zero until approved. Real page fetching is `fetch: http`, which has no provider to meter. `harness/mcp_tools.py` still strips every key, except `TAVILY_API_KEY`, which reaches `knowledge` only.

`local_search` is BM25 on SQLite FTS5. OnIt's hybrid mode needs a local embedding model, and only VAD, Smart Turn, and audio I/O run locally here.

## Alternatives

- **Keep shell and files inside `delegate`, as the review says.** Safer, but nothing runs until the worker is built, and calls bypass the tool policy and the task suite.
- **Run OnIt's own MCP servers.** Apache-2.0 allows it, but they would bring OnIt's Python range (3.10 to 3.12 when last checked) and its own approval flow, and would skip our ledger.
- **Tavily as the default search.** Steadier than `ddgs`, but needs an account. `ddgs` needs no key but scrapes search engines and can break or be blocked. Its use for a product must be reviewed before any sale.

## Consequences

- Spoken confirmation is weaker than reading a command. The model writes the question, so injected text from a fetched page could produce a misleading summary. This is why live `bash` stays behind a flag.
- The working directory and environment limits are not an OS sandbox. A confirmed live command can reach the user's whole account and the network. Add `sandbox-exec` on the Mac and bubblewrap on the Pi before any open demo.
- `fetch_content` checks addresses before connecting, so DNS rebinding can still get past it.
- `index_documents` writes only to its own index. The model can read anything indexed, so documents should hold nothing it must not repeat aloud.
- Open-Meteo's free tier is non-commercial, so it fits the course and not a product.
- More tools enlarge every prompt. See Results.

## Results

On fakes, Nov 1, 2026, with every server offered:

| | Before | After |
| --- | --- | --- |
| Tools offered | 18 | 32 |
| Tool schema size | 4,729 chars | 11,162 chars |
| Task suite | 41 cases | 53 of 53 passed, no spend |

Schemas grow by about 1,600 tokens per LLM call at four characters a token. `workspace` is more than half of that. Each stdio server adds about 0.5 s to startup, and tool listing works on stdio and streamable HTTP.

Fakes pick tools by rule, so these runs say nothing about whether a real model still picks the right tool, or how much the larger prompt costs in latency. All nine servers stay on by default until a live Groq run shows that. Real `ddgs`, Tavily, Open-Meteo, and HTTP fetching have not been run. Live `bash` and `serve` were run only in unit tests.

## Addendum, Nov 1, 2026: `list_files`

Run `20261101-192724-03eda3` asked "What files can you see?". No tool listed files, so the model chose `bash` with `ls -la`, which needs spoken confirmation and is a mock. `list_files` is read only, so it runs at once. It lists both folders, newest first, so "the latest file" is answerable. `~/Downloads` stays outside the roots.

## Sources

- https://github.com/sibyl-oracles/onit/blob/main/docs/TOOLS.md
- https://open-meteo.com/en/terms
- https://docs.tavily.com/documentation/api-credits
- https://pypi.org/project/ddgs/
