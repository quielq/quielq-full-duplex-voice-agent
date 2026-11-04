# ADR-034: Our own command policy for bash

Date: Nov 4, 2026. Status: designed with Quiel on Nov 4, 2026, from his answers to seven questions. Built and tested on this Mac. Not tried live by voice yet.

## Context

ADR-024 vendored OnIt's command parser (Apache-2.0) unchanged, and made every `bash` call ask for a spoken yes. Quiel asked on Nov 4 for a policy designed here, with OnIt as inspiration only and none of its code copied. Two problems with the old rules showed up in use:

- Asking before every `ls` makes a multi-step job slow to talk through.
- The read-back spoke the command's symbols as words ("rm dash rf build and then..."), which is accurate but hard to follow.

## Decision

`servers/command_policy.py`, written from scratch. `servers/vendor/` and `servers/shell_guard.py` are deleted.

| Question | Quiel's answer (Nov 4) | What it does |
| --- | --- | --- |
| What needs a yes? | Only changes and runs | Reads (`ls`, `cat`, `grep`, `find`, `wc`, `git status`) run at once. Writes, deletes, and running code or builds ask. |
| How much shell syntax? | Simple commands only | Words, quotes, pipes, `&&`, `\|\|`, `;`, plain redirects. `$(...)`, backticks, `<(...)`, here-documents, loops, functions, background `&`, `FOO=1 cmd`, and a variable as the program are refused. |
| Which programs? | Read and search, change workspace files, run code and builds, package installs | Three lists: reads, changes, runs. |
| Installs? | Refuse clearly | "The sandbox has no network, so installing packages is off." |
| A program on no list? | Ask with a spoken yes | It asks, and the question names the program. |
| Deletes? | Ask, and keep a copy first | `rm` and `rmdir` ask. The server copies what they delete to `data/workspace/.versions` before running, and the result lists the copies. |
| The read-back? | Plain words, exact text on screen | "This will delete build and everything inside, then run make_site.py with Python. Should I go ahead?" The face shows the exact command while it waits. |

Also refused:

- Network tools, with a pointer to `fetch_content`.
- Desktop openers, with a pointer to `open_in_browser`.
- Shells with `-c`, and `find -exec` and `find -delete`.
- `awk` with `system`, and `xargs` feeding anything but a read.
- Paths outside the workspace: absolute paths (except `/dev/null`), `~`, and `..` that climbs out.

Refused as critical, which counts toward containment (ADR-024): `sudo`, `docker`, `crontab`, disk, kernel, and system-settings commands, `env` and `printenv`, deleting from the root or home, and anything touching `.env`, `.ssh`, or other secret files.

**In the harness:** the `bash` rule uses the `command` guard. A run verdict skips the hold and runs at once. An ask verdict uses the confirm hold and its undo window as before. `serve` uses the `service` guard, so a background job always asks. Its read-back now says what the command will do. The workspace server checks again before it runs anything, so a call that skips the harness gets the same answer.

## What OnIt inspired, and what is different

OnIt gave the idea of classing every program in a command into allow, ask, or deny, and of refusing what the parser cannot read. Everything else is different:

- Our reader is a small tokenizer for simple commands. It does not try to follow `$(...)` or `bash -c` into nested commands, so it refuses them.
- The verdict follows what a command does (read, change, run), not a single allowlist.
- The read-back words come from each program's arguments.
- `rm` copies first.
- Install and network refusals give reasons a listener can act on.

## Alternatives

- **Keep OnIt's file:** it works and is licensed, but Quiel did not want the professor's code in the project.
- **Every command asks (ADR-024 as it was):** safest, and too slow for a multi-step job by voice.
- **Follow nested shell syntax:** more commands would run, but a read-back for `$(...)` cannot honestly say what will happen.

## Consequences

- The sandbox is still the boundary. An interpreter that gets a yes can do anything the sandbox allows.
- The sandbox tests now pass their outside paths inside Python code, because the policy refuses a plain `cat /etc/passwd` before the sandbox sees it.
- Commands a model writes with `$(...)` or loops are refused, and the model has to write them simply. How often that happens on a real job is unmeasured.
- **The model must know a delete asks.** In the first live mic run (Nov 4, `20261104-202417-64248a`), Haiku 5.5 refused five times to delete a folder with `bash`, saying it would be permanent, so the read-back never ran. The `bash` description and the system prompt now say that a change or delete is read back and needs a yes, and that `rm` keeps a copy in `.versions`, so the model calls the tool and the harness asks. A test keeps that text in step with the policy. Not retried live yet.
- The read-backs are only as good as `_describe`. A program it has no phrase for reads as "run cowsay on hi".

## Sources

- ADR-024 (guardrails, sandbox, containment), ADR-028 (`.versions`).
- OnIt, for the idea only: https://github.com/sibyl-oracles/onit
- Bash manual, Simple Commands and Pipelines: https://www.gnu.org/software/bash/manual/bash.html#Shell-Commands
