# ADR-024: Real tools, phone bridge, and shell guardrails

Date: Nov 1, 2026. Status: approved by Quiel, Nov 1, 2026. Extends ADR-021, and departs from the review as ADR-021 does. Nov 4, 2026: the OnIt parser and the rule that every command asks are replaced by our own command policy (ADR-034). The sandbox, read-back, and containment stand.

## Context

After ADR-021, weather and fetching were fakes, messages were a local mock, and `bash` and `serve` were mocks with no OS sandbox. Spoken confirmation was also weak: the model wrote the question, so a page it had read could soften what the user agreed to. Quiel asked for real weather and fetching, for messages and calls through the Mac bridge built for tiny VCM, for guardrails like OnIt's, and for all of it to work on the Pi later.

## Decision

Finish on the Mac and then move to the Pi. Every part below is chosen at run time, so one branch serves both.

**Weather and fetching.** `tool_stages` now defaults to `open_meteo` and `http`. Tests and the task suite force fakes through `FAKE_TOOL_STAGES`. `get_weather` gained a reply template, off with the others. `fetch_content` now also checks the address it actually connected to, which closes the DNS rebinding gap in ADR-021, and ignores proxy settings so that check stays true.

**Read-back.** A confirm tool with `readback` is asked about by the harness, from the real arguments, with no LLM call. Shell symbols are read as words. A yes counts only after the read-back has played. Text over 240 characters is refused, because it cannot be heard in full.

**Phone bridge.** `scripts/mac_phone_bridge.py` is adapted from quielq-vcm. Compared with VCM it binds to loopback by default, needs `--allow-from` to accept the Pi, reads its token from the environment, and limits request size. `comms` reaches only names in `data/contacts.yaml`, and the model sees names and never numbers. The token reaches only `comms`. `phone_bridge` has a notional price, so the daily cap is a limit of 20 sends. A real send stays per run, with `comms: mock` by default.

**Bash and serve.** Four layers, in order: the flag, OnIt's parser with allow, ask, and deny, the read-back, and an OS sandbox. `sandbox-exec` on the Mac and `bwrap` on the Pi allow writes only in the workspace, reads of that, documents, and system files, and no network. `serve` may also listen. No sandbox means nothing runs. Three critical refusals lock `containment.tools` for the session.

## Alternatives

- **Pi first.** Rejected: tools behave the same there, and it would add speakerphone and echo problems to tool testing while tool choice is still unmeasured.
- **OpenWeatherMap, as VCM uses.** Needs an account and a key. Open-Meteo needs neither, and its free tier allows educational use.
- **OnIt's allowlist alone.** OnIt's docs accept that interpreters can run arbitrary code, and rely on containers. The sandbox fills that role here.
- **Containers.** Heavier, and ME3 has no container runtime on either machine.
- **Bridge as an MCP server.** It would have to run on the Mac anyway, and the Pi would need a remote MCP link for two calls.

## Consequences

- Live tests found what fakes could not: a PDF over 2 MB crashed `fetch_content`. PDFs up to 30 MB now load in full, and `fetch_content` returns about 4,000 characters from a chosen `page`, stopping early so a long paper stays quick.
- `doctor` found that `sandbox-exec` treats an allow rule with no paths as allow everything. Fixed, with a test.
- Each read-back replaces an LLM reply, so it costs nothing extra in tokens.
- A user who barges in on a read-back and then says yes has not heard all of it. The undo window is the backstop.
- `sandbox-exec` is deprecated and could be removed from macOS. If so, `NoSandbox` turns `bash` off.
- `bwrap` arguments are checked for shape only. They have never run on a Pi.
- On the Pi, `serve` uses the host network, so a started service has outbound access.
- The bridge link is plain HTTP on the LAN.
- Calls need a click on the Mac, which is a real limit on hands-free use.
- OnIt's Apache-2.0 file is vendored unchanged with its header, which the licence requires.
- A real message, call, or model run has not happened. All of them wait on a separate go-ahead.

## Update, Nov 5, 2026: one weather deadline

In the live run `20261105-195338-eb8cc9`, "Quezon City" was heard as "Caddon City" and `get_weather` gave no answer for 11 s, until the user spoke again and the call was cancelled with no trace in the log. The tool made up to three calls in a row (the place, the place without "City", the forecast), each with a 10 s limit, so it could outlast the policy's 15 s limit. The Open-Meteo lookup usually takes about 1 s, but the same query took 6.1 s once when retried.

The two place lookups now run side by side, and every call gets what is left of one 9 s deadline, at most 7 s each. Running out returns "the weather service is slow right now, try again", which the bot can say. A tool cancelled by a new turn now logs `tool_call_cancelled`, and the run report flags a tool that started and never ended as `tool_no_end`. Checked on fakes, plus three direct calls to Open-Meteo.

## Sources

- https://github.com/sibyl-oracles/onit/blob/main/docs/ISOLATION.md
- https://open-meteo.com/en/terms
- https://openweathermap.org/price
- https://github.com/containers/bubblewrap
- quielq-vcm `scripts/mac_phone_bridge.py`, `docs/DEPLOYMENT.md`
