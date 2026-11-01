# ADR-023: Report held tool results through the LLM

Date: Nov 1, 2026. Status: approved by Quiel, Nov 1, 2026.

## Context

A held tool answers the LLM at once and commits later, so `_write_back` updates the tool message after the turn has ended. Nothing then asks the LLM to read it. In run `20261101-192724-03eda3` a confirmed `bash` call returned "Nothing ran", and the user never heard it. The model went on saying the tool was waiting. Live `bash` output would be lost the same way.

## Decision

When a held tool succeeds, `ToolPolicy` starts one more LLM reply. `run_bot` supplies `policy.report`, which appends a fixed user-role note with `run_llm=True`. The note says the action finished and asks for the outcome in one short sentence.

The result stays in the tool message. The note is fixed text, so output from a tool never gets the user role.

This is on for every held tool. `report_result: false` under `tools` in `configs/tool_policy.yaml` turns it off for one tool. Failures keep the fixed `failed` phrase, with no LLM call. Cancelled, denied, and expired actions start no report. Each event is a `result_reported` decision in the event log.

## Alternatives

- **A fixed phrase such as "Done."** No LLM cost, but it cannot carry `bash` output.
- **Reports for `bash` only.** One call less on simple tools, but "write the file" would still end in silence.
- **Put the result in the note.** The model would see it last, but fetched or file text would arrive as if the user had said it.

## Consequences

- Each held success costs one more LLM call, about 5,000 prompt tokens with all servers on.
- A user message that is not the user's is in the context. It is marked as automatic, and the model may still mention it or call a tool.
- The report does not wait for the user to stop talking. It plays over them until a barge-in stops it, as the `failed` phrase does.
- Tested on fakes only. No live model has been run on the note, so its effect on wording and tool calls is unknown.

## Sources

- Pipecat 1.13.0, installed package: `pipecat/frames/frames.py`, `LLMMessagesAppendFrame`
