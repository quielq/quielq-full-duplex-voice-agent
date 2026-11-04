# ADR-039: Document agents, and a builder for custom ones

Date: Nov 4, 2026. Status: built and tested on fakes only. No live LLM has answered from the documents yet.

## Context

Quiel wants several agents to choose from when the app opens:

- **The generalist:** the assistant with every tool, as built so far.
- **One agent per document set:** company policy, IT policy, contract law, the customer list, and the workout files. Each should behave like a RAG model that answers only from its own folders.
- **Custom agents:** built by the user from a name, a model, a set of documents, and a colour.

Each agent's avatar should have its own colour. All of it has to fit on the Pi without running it hotter (ADR-030).

## Decision

- **Agents.**
  - `configs/agents.yaml` holds the built-in agents and the colour palette.
  - Custom agents are saved to `data/agents/custom.json`, and the last pick to `data/agents/active.json`. Neither is in git.
  - `harness/agents.py` loads, checks, and saves them.
- **What an agent changes:** the prompt, which task servers start, which documents the docs server may see, and the LLM model. VAD, turn detection, STT, TTS, and the tool policy are the same for every agent.
- **A document agent:**
  - starts only the docs server, with `ME3_DOC_SCOPE` set to its folders. The variable is passed to that server alone and is never inherited.
  - is offered only `local_search`, `search_document`, `list_documents`, and `read_document`.
  - is told to look up every question, answer only from tool results, name the document, and say when the documents do not cover the question.
- **The scope is enforced by the server, not the prompt.** With `ME3_DOC_SCOPE` set:
  - every docs tool resolves paths against the scoped folders only;
  - search results are filtered by path prefix in SQL, using `substr`, not `LIKE`, because folder names contain `_`;
  - a scoped server never wipes the shared index;
  - a model asking for another folder gets "outside this agent's documents".
- **Retrieval stays BM25 on SQLite FTS5 (ADR-021).** There is no embedding model and no vector store, so nothing new runs locally.
  - CSV rows are indexed one line per row with their column names, so a chunk about a customer still says which column is which.
  - `read_document` reads a short file whole, 12,000 characters a page, so the model can count or compare rows. The 50-row customer list fits in one page.
  - A scoped server brings its own files up to date once per process.
- **Sample documents.**
  - `assets/sample_documents/` holds a fictional company's handbook, IT and security policy, contracts handbook, and 50-row customer list. They are made up, so they can be public.
  - The contracts handbook simplifies general Philippine Civil Code principles and says it is not legal advice.
  - On start, any sample folder missing from `data/documents` is copied in. A folder that is there is left alone, so a file deleted on purpose stays deleted.
  - Quiel's workout files moved to `data/documents/workouts/` and stay out of git.
- **Models.**
  - An agent's model is `direct`, which keeps the run's own LLM stage, or an OpenRouter model from the priced list (ADR-040).
  - A model applies only on a live run. With the LLM stage on `fake`, every agent stays fake, so picking an agent never spends anything unasked.
  - The generalist is `direct`, which means Haiku 5.5 on Anthropic for live runs (ADR-025).
  - Each session can run on another listed model, picked under the agent in the picker or with `--model` on a scripted run. The pick is kept per agent in `active.json` and changes only that session's model, not the agent (ADR-040 update).
- **Switching.**
  - With the face on and a live mic, `run_agents` keeps one face up across sessions. Picking an agent ends the session (`worker.cancel`), and the next session starts with that agent: a fresh context, its own servers, its colour, and a "Now talking to" line in the chat.
  - A scripted run (`--input text` or `wav`) uses `--agent` or the default, never the face's last pick, so tests and benches are not changed by what someone tapped.
- **The builder.**
  - It can set a name of up to 32 characters, a short description, a model from the priced list or `direct`, a colour from the palette, and folders or files under `data/documents`.
  - Every custom agent is a document agent. The builder cannot add tools, change the tool policy, or raise a cap.
  - The server checks each field and refuses with a reason the page shows, for example a path outside the documents folder, a colour not in the palette, an unpriced model, or a built-in name.
  - At most 20 custom agents. A hand-edited bad entry is skipped, not fatal.
  - The routes use the same same-site check as the mute, and the face stays on 127.0.0.1.
- **Colour.** The face's glow, lens, and accents come from one CSS variable set from the agent's colour. The edge and shade colours are derived with `color-mix`, so there is no image work. Since the Nov 4 update the shell is mixed from it too, and the picker, the agent chip, and the builder's colour choices each show a small static robot in the agent's colour.

## Alternatives

- **Embeddings with a local model:** better recall on paraphrased questions, but a model running on the Pi is exactly what the heat budget rules out. A cloud embedding API adds a fourth paid provider and an index to rebuild. BM25 plus `read_document` covers these small, keyword-rich documents.
- **Scope by prompt only:** the model could still search every folder. A tool that cannot see the files is the real boundary.
- **Hot-swap the LLM in one pipeline (Pipecat's `LLMSwitcher`):** keeps the audio warm, but the tools, the prompt, the context, and the docs server scope also change. Restarting the session is simpler and leaves no state behind. It takes a few seconds, mostly MCP servers starting, and fewer start for a document agent.
- **Let custom agents pick tools:** a builder that grants tools is a builder that can grant `bash`. Not asked for, and it would need its own ADR.

## Consequences

- A document agent's answer is only as good as BM25's top five chunks or a whole-file read. Questions phrased very differently from the text, or counts over files larger than one page, may miss. The task suite has no live document cases yet. They need approval, since they use a real LLM.
- The 20 MB workout PDF is mostly images, and its text is extracted once and then cached by mtime. The first search after it changes takes a few seconds.
- A custom agent's documents are checked when it is saved and again when it is loaded. A folder deleted later leaves the agent with nothing to search, and it says so.
- Not run on the Pi. The face's agent routes are tested with aiohttp's test client only.

## Sources

- ADR-021 (docs server, BM25), ADR-025 (Haiku), ADR-027 (robot face), ADR-030 (Pi layout), ADR-040 (OpenRouter).
- Pipecat 1.13.0, installed: `pipecat/pipeline/worker.py` (`PipelineWorker.cancel`), `pipecat/pipeline/llm_switcher.py`.
- SQLite FTS5: https://www.sqlite.org/fts5.html
