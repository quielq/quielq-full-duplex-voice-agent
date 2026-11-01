# ADR-019: ForceEndTurn with Smart Turn, built but not the default

Date: Nov 1, 2026. Status: accepted. `turn.stop: flux_forced` exists and stays off.

## Context

ADR-018 found a floor of about 1.0 s on the Flux wait that its settings do not move. Deepgram documents `ForceEndTurn`: with `eot_threshold=1.0`, a local detector can end a Flux turn. Pipecat 1.13 does not send it. We built that path in `harness/flux_forced.py` and tested it with `bench/eot_sweep.py`, on the same 12 clips as ADR-018.

The rules were fixed before the runs. `flux_forced` becomes the default only if it saves at least 300 ms p50 on both commands and questions, cuts no more than 6 of 12 pause clips, loses no word on complete clips, and no run stalls.

## Results

Wait is from the last loud sample to `end_of_turn`, p50, 12 clips per kind. Flux is the baseline from the first comparison, in the run before the fix.

| | Commands | Questions | Pause clips cut | Stalls |
| --- | --- | --- | --- | --- |
| `flux` | 1234 ms | 1030 ms | 6 of 12 | 0 |
| `flux_forced`, before the fix | 851 ms | 728 ms | 3 of 12 | 2 |
| `flux_forced`, after the fix | 862 ms | 836 ms | 5 of 12 | 0 |

After the fix, commands save 372 ms and questions save 194 ms, under the 300 ms bar. The 302 ms saving on questions before the fix is the same code path, so the two runs show how much these numbers move between runs. No word was lost on any complete clip.

Groq `gpt-oss-20b` with fake TTS, 2 repeats, both arms in one run. Reply is from the last loud sample to the first TTS byte, p50.

| | Commands | Questions | Pause clips | Worst wait |
| --- | --- | --- | --- | --- |
| `flux` | 2562 ms | 1760 ms | 2452 ms | 3113 ms |
| `flux_forced` | 2390 ms | 1894 ms | 4026 ms | 5702 ms |

With a real LLM, the forced path saved 172 ms on commands, lost 134 ms on questions, and lost 1574 ms on pause clips. Pause clips were cut equally, 4 of 8 each. One forced turn ended on the Flux timeout.

`ForceEndTurn` to the final transcript took 490 to 530 ms p50 in the rerun and 330 to 350 ms in the Groq run, with single runs up to 1537 ms. That round trip is most of what remains of the wait.

## The stall

Two runs stalled for 5 s before the fix, and a deliberately delayed Flux connection reproduced it every time. When Flux's `StartOfTurn` arrives after local VAD has heard the speech end, its proposed turn start marks the user as speaking, and `UserTurnController` refuses to stop a turn while the user is speaking. The inference runs, the turn never finalizes, and the 5 s watchdog ends it. The tool result is then not spoken in time. This is the symptom ADR-005 saw. A slow connection triggered it here, and a very short word could too, since that also ends before Flux reacts.

The fix drops Flux's turn proposals in this mode, so local VAD alone owns the turn, and holds a force that arrives before `StartOfTurn` until Flux opens a turn. Delays of 0.2 s to 1.3 s no longer stall, and the 36-run rerun had none.

## Decision

Keep `turn.stop: auto`, which is Flux's own end of turn. `flux_forced` fails the 300 ms rule on questions, and with a real LLM it is no clear gain and much worse on mid-sentence pauses. The code stays, off by default, because it is small and tested.

## Alternatives

- Adopt it for the 372 ms on commands. Rejected: that moves the bar after the results, and the Groq run does not show that saving in replies.
- Delete the code. Rejected: it is the only route found so far under the Flux floor, and a shorter Smart Turn silence limit might fix its tail.
- Lower Smart Turn's `stop_secs` from 3 s. Not tried. Waits of 2.4 s to 5.7 s look like Smart Turn calling a turn incomplete and falling back to that limit, but this was not confirmed turn by turn.

## Consequences

- End of turn stays at about 1.0 s to 1.25 s on the Mac. The next gains must come from elsewhere: the gap after a tool call, the LLM, or Nova-3 with Smart Turn.
- Flux is the only turn detector in use, so Smart Turn's cost on a Pi core matters less unless Nova-3 returns.
- The subclass uses Pipecat private names, listed in `harness/flux_forced.py`. Recheck them on upgrade.
- These are framework timings on synthetic speech, from one network on one day. In the Groq run even plain Flux waited up to 3.1 s, so network swings are large. Neither mode has run on a live mic.

## Sources

- https://developers.deepgram.com/docs/flux/own-turn-detection.md
- Installed source: `pipecat/turns/user_turn_controller.py` (`_trigger_user_turn_stop`), `pipecat/turns/user_stop/turn_analyzer_user_turn_stop_strategy.py`
- Rows in `logs/eot_sweep-20261101-125235.jsonl`, `-130835`, `-131421` (local only, not committed)
