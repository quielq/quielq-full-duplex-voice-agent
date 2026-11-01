# ADR-017: Groq 20b versus 120b, first task suite run

Status: results recorded, default model not changed. Oct 31, 2026.

## Context

The decision rule in `docs/architecture.md` compares models on tool-call success, `stay_silent`, stability, and time to first token. This is the first run of both Groq models through `bench/task_runner.py`, on the laptop, with every server's tools offered (`--all-servers`), fake STT and TTS, and Groq's free tier. The suite has 41 cases, not yet the planned 40 tool turns plus 20 non-addressed utterances.

`bench/task_runner.py` gained `--model` so a run can change the model without editing `configs/providers.yaml`.

## Results

| | gpt-oss-20b | gpt-oss-120b |
| --- | --- | --- |
| Runs that reached a verdict | 160 of 246 | 205 of 205 |
| Passed | 142 (88.8%) | 194 (94.6%) |
| Side talk stays silent | 4 of 8 | 10 of 10 |
| Malformed tool calls from the model | 2 | 0 |
| Doubled tool calls | 3 | 0 |
| Time to first token, p50 | 902 ms | 2554 ms |
| Time to first token, p95 | 7238 ms | 5994 ms |
| Cost per passed case | $0.00020 | $0.00039 |

The 20b column combines one pass and five repeats. 86 runs hit Groq's free-tier limit of 200,000 tokens per day on 20b and are excluded, so the later cases have fewer 20b runs and 20b is not a clean five repeats. A pass costs about 55,000 tokens, so five repeats do not fit in one free-tier day. 120b never hit a limit.

Time to first token is a laptop figure on the free tier, measured from `llm_request` to `llm_first_token` in the event log. It is not the Pi measurement the rule asks for, and it does not separate queueing from reasoning. Both models miss the 350 ms and 700 ms bars here.

## The 11 failures on 120b

- 5 are `conv_undo_keeps_timer` and `notes_conv_undo_keeps_note`. The undo word arrived more than 3.0 s after the hold in exactly these 5 runs, and within 3.0 s in the other 5. `_undo_window` starts when the tool is held, and the reply announcing the hold had not arrived yet. In run `20261031-190720-8bf32e` the action committed at 3.01 s and the first token came at 3.21 s. This is a product bug: a slow reply uses up the undo window before the user hears about it. The cases use 3 s; the 5 s default narrows the gap but does not close it (slowest first token 15.3 s).
- 5 are `addr_short_follow_up_in_active`, which also fails every 20b run. The case expects the reply to contain "thanks", and the fake LLM's wording does not carry over to a real model. "You're welcome" is a fine reply, so the case is too strict. Both models also repeat the time, which is worth a separate look.
- 1 is a `conv_set_list_cancel` timeout.

## Decision

Keep `openai/gpt-oss-20b` as the configured default until Quiel chooses. On accuracy, 120b is ahead: all next-reply and tool-call cases passed, and every side-talk case stayed silent. Accuracy does not settle it, because 120b's median first token is about 1.7 s slower on this measurement.

## Alternatives

- Switch to 120b now: better accuracy, but it adds latency to a loop already at about 2.8 s.
- Move to Groq's paid Dev Tier for a clean 20b run: needs Quiel's approval. Waiting for the daily token window also works, over two days.

## Undo window fix

The undo window now opens when the user has heard about it. `TurnEventObserver` tells `ToolPolicy` when a reply with text ends and when the bot stops speaking. The bot stopping before the reply has ended does not count, so a waiting phrase cannot open the window. After a spoken yes, the window waits for the bot to start and stop, so the cut-off question does not count. If nothing is announced within `announce_wait_s` (30 s), the action is dropped and the failed phrase is spoken: a destructive action never runs on a window the user did not know about. A word from the undo list works from the moment of the hold.

Rerun on 120b, Nov 1, cost $0.0046: `conv_undo_keeps_timer` and `notes_conv_undo_keeps_note` passed 5 of 5 each, up from 5 of 10 together. In 4 of the 10 runs the undo word came more than 3 s after the hold (3.77 s to 8.01 s), which would have failed before the fix. Text mode, fake STT and TTS, so this does not cover live audio timing.

## Consequences

- Text mode has no playback, so there a finished reply counts as heard.
- A barge-in during the announcement stops the bot, which opens the window. The user has heard at least part of it.
- If a waiting phrase ends after the reply has been generated but before its audio arrives, the window opens early by that gap. Not tested.
- `addressing.yaml` has now run on a real LLM, but only 7 cases. The 90% `stay_silent` bar needs the 20 planned utterances.
- Latency needs its own check before any model choice is final, including `reasoning_effort` and timing on the Pi.

## Source

Rate limit text from Groq's 429 response in the run log. Prices from `configs/providers.yaml`; the 120b price is from trackers and still needs a recheck on https://console.groq.com/pricing.
