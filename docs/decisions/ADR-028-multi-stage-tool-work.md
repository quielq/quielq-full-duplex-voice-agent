# ADR-028: Let file tools keep pace with multi-stage jobs

Date: Nov 2, 2026. Status: built. Requested by Quiel on Nov 2, 2026. One live text run on Haiku passed, below. Not yet run by voice.

## Context

Quiel wants the existing tools to carry complex jobs, such as planning and building a website. `write_file` and `edit_file` are destructive and closed world, so each was held for a 5 s undo window after being announced. That fails a multi-step job: the file does not exist for the next step, the model must stop and speak after every file, and the window is 5 s of a speech loop's attention for a file nobody has seen. Pipecat's default `max_tokens` of 4,096 could also cut off a whole page inside one tool call. CLAUDE.md says destructive tools default to an undo window, so this departs from it.

## Decision

- `write_file` and `edit_file` run at once (`permission: auto`, `undo_s: 0`).
- The server copies a file to `data/workspace/.versions` before replacing or editing it, and the result names the copy. That gives a longer undo than the window did.
- Anthropic `max_tokens` is 8,192, from `configs/providers.yaml`. Cost follows what is used, not the limit.
- The system prompt allows one spoken sentence before a job of several steps, and tells the model to work to the end in a folder of its own, starting with `plan.md`.
- `write_file` and `edit_file` stay on the containment list, and stay confined to the workspace.
- The bot now says when a timer or alarm is due (`harness/timer_watch.py`). This is the same request: the tool was storing a time that nothing ever acted on.

`bash`, `serve`, and `open_in_browser` keep their read-back and spoken yes.

## Live result

Run `20261102-092709-15e3b2`, Nov 2, 2026, Haiku 5.5, text input, fake TTS and fake tools, one run, $0.0061. The request was to plan and build a small website about red pandas.

Haiku opened with one sentence, wrote `plan.md`, `style.css`, and `index.html` in their own folder, read the stylesheet back, and said it was done, with no prompting between steps. It took 19.2 s from request to last reply. The page linked its stylesheet and rendered correctly in a browser, with no console errors. The first model reply took 6.9 s to end, and each file took about 4 s.

One small flaw: it tried to read `style.css` before the file existed, got an error, and carried on.

This is one run of a small site, with no `bash` or `serve`, so it shows the path works, not how reliable it is. A plain `task_runner` case ended at the opening sentence, so the case now pauses to let the job finish.

## Alternatives

- **One yes for a whole job:** closer to how a person would delegate, but a spoken yes would then cover commands nobody has heard, which is what ADR-024 rules out.
- **Hold only overwrites:** a long job still stalls when it revises its own file.
- **Build `delegate` first:** a heavier agent may suit big jobs better, but the request was to make the existing tools work.

## Consequences

- A misheard request can replace a workspace file with no chance to say cancel. The old version is kept, and the workspace was already documented as a place to keep nothing you cannot lose.
- `.versions` grows without limit. It is small text for now.
- A job that needs a command asks for a yes each time, so builds that use `bash` remain slow by design.
- Silence during a long job is expected. The working face on the display shows the current step, and the audio-only path has only the opening sentence.
- Reliability on Haiku is unknown after one run. `workspace_plan_and_build_a_site` measures it, in paid runs.

## Sources

- Pipecat 1.13.0, installed package: `pipecat/services/anthropic/llm.py` (default `max_tokens` 4096)
- ADR-011, ADR-024
