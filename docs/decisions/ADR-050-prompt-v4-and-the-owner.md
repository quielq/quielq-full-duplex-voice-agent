# ADR-050: Prompt v4, the owner's name, and better document hits

Status: accepted. Quiel approved the plan, the contents of `configs/owner.yaml`, and a paid test on Nov 16, 2026. Sources: https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/use-xml-tags, https://docs.vapi.ai/prompting_guide, https://docs.livekit.io/agents/build/prompting, https://developers.deepgram.com/docs/keyterm, https://deepgram.com/pricing.

## Context

The class demo asked the agent to search for Quiel's profile online and build a website from it. Four things were in the way.

- The v3 prompt was one paragraph. It gave one sentence to building, nothing on researching a person, and nothing on the documents folder, although the generalist has the document tools.
- The agent did not know who it belongs to, so "a website about me" had no subject.
- The name is said "ki-yel ki-wa". Flux had no reason to write it as "Quiel Quiwa", and Cartesia read the spelling its own way.
- Document search matched exact words only, and a hit named a file but not the title or the section, so the model could not say where an answer came from.

## Decision

**Prompt v4** (`system_prompt` in `harness/bot.py`, `PROMPT_VERSION = "v4"`).

- Eight sections in tags: role, speech, hearing, tools, research, documents, building, publishing. The tags are for the model and are never spoken. Anthropic's guidance is that tags keep instructions and data apart, and the voice guides from Vapi and LiveKit use the same labelled sections.
- Every v3 rule is kept, most with its reason beside it.
- New: who the owner is, how to research a person without inventing facts, how to look things up in the documents, and a six step build procedure (say, gather into `notes.md`, plan, write, check, report) that ends in `publish_site` and `open_published_site` (ADR-049).
- `notes.md` exists because the context keeps only the last 20 turns (ADR-047), and a build can use more than that in tool calls.

**The owner** lives in `configs/owner.yaml`: name, a one line description, how the name is said, likely mishearings, search hints, keyterms, and IPA. The file is public, so it holds only what the README already says. Three places read it.

- The prompt, through `harness/owner.py`.
- Flux, as keyterms, when `providers.deepgram.keyterms` is true. Deepgram lists Keyterm Prompting as a streaming add-on at $0.0013 a minute, so the cost observer records `stt_keyterm_minutes` at `deepgram_keyterm_per_min` on top of the Flux rate.
- Cartesia, as inline IPA, through Pipecat's `pronunciation_transform_ipa`.

**Spelling comes back.** Cartesia reports the markup it was sent as the spoken word: a probe on Nov 16 returned `<<k|i|j|ˈ|ɛ|l>>` for "Quiel". Those words become the context and the chat. `harness/pronounce.py` (`SpelledBack`) maps each one back to the real word, one for one, so truncation by word index (ADR-006) is unchanged.

**Document search** (`servers/tasks/docs/server.py`).

- The index uses SQLite's Porter stemmer, so "renewals" finds "renewal". An index from before this change is dropped and rebuilt on first use.
- Each hit carries `title` (the document's first heading, or its file name in plain words) and `section` (the heading over the matched text).
- It stays keyword search. No embeddings, no model, no cost, and nothing new to install on the Pi.

## What a live run showed

One text run on Nov 16, 2026, on Haiku 5.5 and Tavily (run `20261116-102716-6dc45b`, about $0.03 with the search): "Search for Kiel Kiwa's profile online and build a one page website about me."

- The model searched `"Quiel Quiwa" UP Diliman AI`, so the misheard name was corrected and the hints were used.
- It wrote `plan.md` and a 3,500 character `index.html` with its own styles and a phone layout, read it back, said it was done, and offered to open it. The whole job took 21 seconds.
- Every fact on the page came from the search results. The LinkedIn page itself returned no text to `fetch_content`.
- It skipped `notes.md`, and it still said a short sentence before some tool calls, against the prompt. An earlier wording had it write files with a `bash` here-document, which the command policy refused, so the prompt now says to use `write_file`.
- A scripted text run ends at the first reply that has words, so a build has to be run with a pause after the request (`turns=[text, seconds]`), or by voice.

## Alternatives

- Put the mishearing list in the prompt only: free, but the recogniser still writes the wrong name into the chat, and the voice still says it wrong.
- Respell the name for the voice ("Kiyel Kiwa"): simpler, but IPA is exact and Pipecat already formats it for Cartesia. If the IPA sounds wrong, change it in `owner.yaml`.
- A Cartesia pronunciation dictionary: it lives in the Cartesia account, so the repo would not show how the name is said.
- Embedding search for documents: better recall on paraphrases, but it needs a local model or a paid call, against the rule that only VAD, Smart Turn and audio run locally.
- More about the owner in the prompt: the repo is public, and the demo asks the agent to find the profile, not to recite it.

## Consequences

- The prompt is about 870 words, up from about 330. It is cached on Anthropic, so the cost is mostly a one time write per session, but first token time should be measured on a live run before and after.
- Keyterms add about 20 percent to the speech recognition cost. Turn them off with `keyterms: false`.
- The mishearings in `owner.yaml` are guesses until a mic run shows what Flux really writes.
- The pronunciation applies to Cartesia only. Aura-2 says the name as written.
- Only single word IPA entries are used, so the word count never changes.
- Whether the IPA sounds right has to be judged by ear: `data/name_probe/` holds three clips from the Nov 16 probe (as written, IPA, respelled).
