# ADR-026: Open workspace HTML in the browser, outside the sandbox

Status: accepted, Quiel approved building it on Nov 2, 2026. Source: `uv run python -m bench.run_report 20261102-013828-01e81b` (turns 4 to 7).

## Context

In the Nov 2 live run the agent wrote `hello_world.html` and could not show it. `open` inside `sandbox-exec` fails with "No application knows how to open URL" (error -10661), because the profile denies the system services that launch apps. The sandbox is right to do that, so the shell can never open a browser.

## Decision

Add `open_in_browser(path)` to the workspace server. It runs the system opener (`open` on the Mac, `xdg-open` on the Pi) on one file, as an argument list with no shell.

- The file must resolve inside `data/workspace` and end in `.html` or `.htm`. The documents folder is not allowed.
- It is destructive and open world, so it needs a read-back from the real path, a spoken yes, and the undo window.
- It is a mock unless `ME3_WORKSPACE_LIVE=1`, and containment locks it with `bash` and `serve`.
- The opener gets only the variables it needs to reach the desktop, so no key in the server's environment passes through it.
- `open`, `xdg-open`, and `osascript` stay refused in `bash`, and the sandbox is unchanged.

## Alternatives

- Allow launch services in the sandbox profile: any command could then start any app, which removes the boundary.
- Put `open` on the shell list: same result, and it would take any file or URL.
- Serve the page with `serve` and have the user open the address: safe, but not hands-free.
- Keep refusing and only say the file name: safe, but the request fails, as it did in the run.

## Consequences

- A page runs in the user's real browser with network access. A model steered by text from the web could write a page with a script, and the agent could then ask to open it. The read-back gives the file name, not the contents, so the yes is the only check on that. The browser's own limits on `file://` pages still apply.
- This is the one way out of the sandbox, so widening it to other file types or to URLs needs a new ADR.
- `xdg-open` has not run on a Pi yet.
