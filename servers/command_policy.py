"""Which shell commands run at once, which need a spoken yes, and which are refused (ADR-034).

Written for this project. The sandbox (servers/sandbox.py) is the boundary; this decides what
to ask a person first, and says it in plain words built from the command itself.

* Reads run at once: listing, reading, searching, counting.
* Changes and runs ask: anything that writes a file, deletes, or runs code or a build.
* A program on no list asks too, and says so.
* Refused, with a reason: installs and network tools (the sandbox has no network), anything
  that reaches past the workspace or into the system, and syntax this reader does not follow:
  ``$(...)``, backticks, ``<(...)``, here-documents, loops, functions, background ``&``, and a
  variable as the program.

Only simple commands are read: words, quotes, pipes, ``&&``, ``||``, ``;``, and plain
redirects. Anything else is refused rather than guessed at, so what the read-back says is
what runs. The workspace server checks again before it runs anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

RUN, ASK, REFUSE = "run", "ask", "refuse"
CRITICAL = "critical"

NO_NETWORK = "The sandbox has no network, so installing packages is off. Use what is already installed."
NO_FETCH = "The sandbox has no network. To read a web page, use fetch_content."
NO_DESKTOP = "The shell has no desktop. Use open_in_browser for an HTML file. Nothing here opens other apps."
TOO_COMPLEX = "I only run simple commands: words, quotes, pipes, and, or, semicolons, and plain redirects."

# What each program does, for the verdict. Reads never ask.
READS = frozenset("""
    ls cat head tail wc grep egrep fgrep rg find tree pwd echo printf sort uniq cut tr diff cmp
    file stat du df which basename dirname realpath readlink date true false test [ jq column nl
    md5 md5sum shasum sha256sum xxd od hexdump strings seq yes expr comm paste fold fmt rev tac
""".split())
CHANGES = frozenset("mkdir touch cp mv rm rmdir ln chmod tee unzip zip tar gzip gunzip patch truncate".split())
RUNS = frozenset("""
    python python3 node deno npm npx make pytest gcc cc clang c++ g++ go cargo rustc javac java
    ruby perl php tsc awk gawk sh bash zsh uv git sqlite3 bc
""".split())

INSTALLERS = frozenset("pip pip3 pipx brew apt apt-get dnf yum pacman apk conda mamba gem yarn pnpm bun".split())
INSTALL_VERBS = {
    "npm": {"install", "i", "ci", "add", "update", "uninstall"},
    "uv": {"add", "pip", "sync", "tool", "remove", "lock"},
    "cargo": {"install", "add", "fetch", "update"},
    "go": {"install", "get"},
    "python": {"pip", "ensurepip"},  # python -m pip
    "python3": {"pip", "ensurepip"},
}
NETWORK = frozenset("curl wget ssh scp sftp rsync nc ncat netcat telnet ftp ping dig nslookup host gh".split())
GIT_READS = {"status", "log", "diff", "show", "branch", "blame", "ls-files", "rev-parse"}
GIT_NETWORK = {"clone", "fetch", "pull", "push", "remote", "submodule"}
DESKTOP = frozenset("open xdg-open osascript".split())
SYSTEM = {
    "sudo": "acting as another user", "su": "acting as another user", "doas": "acting as another user",
    "dd": "writing raw disks", "mkfs": "changing disks", "fdisk": "changing disks", "parted": "changing disks",
    "diskutil": "changing disks", "mount": "changing disks", "umount": "changing disks",
    "shutdown": "stopping the machine", "reboot": "stopping the machine", "halt": "stopping the machine",
    "poweroff": "stopping the machine", "kill": "stopping processes", "killall": "stopping processes",
    "pkill": "stopping processes", "launchctl": "changing system services", "systemctl": "changing system services",
    "crontab": "scheduling commands", "at": "scheduling commands", "chown": "changing owners",
    "chflags": "changing file flags", "csrutil": "changing system security", "spctl": "changing system security",
    "tccutil": "changing system security", "defaults": "changing system settings", "security": "reading the keychain",
    "sysctl": "changing the kernel", "insmod": "changing the kernel", "rmmod": "changing the kernel",
    "modprobe": "changing the kernel", "kextload": "changing the kernel", "docker": "running containers",
    "podman": "running containers", "eval": "running text as a command", "exec": "replacing the shell",
    "source": "running a file in the shell", ".": "running a file in the shell", "env": "showing the environment",
    "printenv": "showing the environment", "export": "changing the environment", "alias": "changing the shell",
    "trap": "changing the shell", "nohup": "running in the background", "disown": "running in the background",
}  # fmt: skip
SECRETS = re.compile(r"(^|/)\.(env|ssh|aws|gnupg|netrc|config/me3)(/|$)|(^|/)\.env\.|id_rsa|id_ed25519")
WRAPPERS = {"time", "nice", "timeout", "xargs"}  # these run the program after their own options
SHELL_WORDS = {"for", "while", "until", "if", "then", "else", "elif", "fi", "do", "done", "case", "esac",
               "function", "select", "{", "}", "(", ")", "[[", "]]", "!", "coproc"}  # fmt: skip
OPERATORS = ("&&", "||", "|", ";", "&")
REDIRECTS = (">>", ">", "<", "2>>", "2>", "&>", ">&", "2>&1")

FRIENDLY = {"python": "Python", "python3": "Python", "node": "Node", "deno": "Deno", "ruby": "Ruby",
            "perl": "Perl", "php": "PHP", "bash": "bash", "sh": "the shell", "zsh": "the shell",
            "go": "Go", "java": "Java"}  # fmt: skip


@dataclass(frozen=True)
class Decision:
    verdict: str                 # run, ask, or refuse
    reason: str = ""             # why, for a refusal or an unlisted program
    words: str = ""              # what the command does, in plain words, for the read-back
    programs: tuple[str, ...] = ()
    deletes: tuple[str, ...] = ()  # workspace paths a yes would delete, to copy first
    severity: str = ""
    unlisted: tuple[str, ...] = ()  # programs on no list, named in the question


class Unreadable(ValueError):
    pass


@dataclass
class _Simple:
    words: list[str] = field(default_factory=list)
    writes: list[tuple[str, str]] = field(default_factory=list)  # (operator, target)
    reads: list[str] = field(default_factory=list)


def split(command: str) -> list[tuple[_Simple, str]]:
    """Each simple command with the operator after it. Raises Unreadable for anything else."""
    for marker in ("$(", "`", "<(", ">(", "<<", "${"):
        if marker in command:
            raise Unreadable(TOO_COMPLEX)
    out: list[tuple[_Simple, str]] = []
    cur, word, quoted, pending = _Simple(), [], False, ""
    i, n = 0, len(command)

    def end_word():
        nonlocal word, quoted, pending
        if word or quoted:
            text = "".join(word)
            if pending:
                (cur.reads.append(text) if pending == "<" else cur.writes.append((pending, text)))
                pending = ""
            else:
                cur.words.append(text)
        word, quoted = [], False

    while i < n:
        c = command[i]
        if c == "'":
            j = command.find("'", i + 1)
            if j < 0:
                raise Unreadable("A quote is not closed.")
            word.append(command[i + 1 : j])
            quoted, i = True, j + 1
            continue
        if c == '"':
            j, buf = i + 1, []
            while j < n and command[j] != '"':
                if command[j] == "\\" and j + 1 < n:
                    j += 1
                buf.append(command[j])
                j += 1
            if j >= n:
                raise Unreadable("A quote is not closed.")
            word.append("".join(buf))
            quoted, i = True, j + 1
            continue
        if c == "\\" and i + 1 < n:
            word.append(command[i + 1])
            i += 2
            continue
        if c in " \t":
            end_word()
            i += 1
            continue
        if c == "\n":
            c = ";"
        op = next((r for r in sorted(REDIRECTS, key=len, reverse=True) if command.startswith(r, i)), None)
        # "2>" only counts at the start of a word; "a2>b" is not a redirect of stream 2.
        if op and op[0] == "2" and word:
            op = None
        if op:
            end_word()
            if pending:
                raise Unreadable(TOO_COMPLEX)
            if op == "2>&1":
                i += len(op)
                continue
            pending = "<" if op == "<" else op
            i += len(op)
            continue
        op = next((o for o in OPERATORS if command.startswith(o, i)), None)
        if op or c == ";":
            end_word()
            if pending:
                raise Unreadable("A redirect has no file.")
            op = op or ";"
            if op == "&":
                raise Unreadable("I don't run commands in the background. Use serve for a long-running one.")
            if not cur.words:
                raise Unreadable(TOO_COMPLEX)
            out.append((cur, op))
            cur = _Simple()
            i += len(op)
            continue
        if c in "(){}":
            raise Unreadable(TOO_COMPLEX)
        word.append(c)
        i += 1
    end_word()
    if pending:
        raise Unreadable("A redirect has no file.")
    if cur.words:
        out.append((cur, ""))
    elif out and out[-1][1] != ";":
        raise Unreadable(TOO_COMPLEX)
    if not out:
        raise Unreadable("There is no command.")
    return out


def _path_problem(path: str) -> Decision | None:
    if SECRETS.search(path):
        return Decision(REFUSE, "Blocked: that reads secrets.", severity=CRITICAL)
    if path in ("/dev/null", "-"):
        return None
    if re.fullmatch(r"(/|~|\$HOME)/?\*?|/\.\*?|~/\.\*?", path):
        return Decision(REFUSE, "Blocked: that would reach the whole disk or the home folder.", severity=CRITICAL)
    if path.startswith("~"):
        return Decision(REFUSE, "Paths stay inside the workspace, and the home folder is outside it.")
    p = PurePosixPath(path)
    if p.is_absolute():
        if re.match(r"/dev/(sd|hd|nvme|vd|xvd|disk|mem|kmem)", path):
            return Decision(REFUSE, "Blocked: writing to a device.", severity=CRITICAL)
        return Decision(REFUSE, "Paths stay inside the workspace. Use a path relative to it.")
    depth = 0
    for part in p.parts:
        depth += -1 if part == ".." else (0 if part == "." else 1)
        if depth < 0:
            return Decision(REFUSE, "Paths stay inside the workspace, and that one leaves it.")
    return None


def _args(words: list[str]) -> list[str]:
    """The operands: no options, and no bare numbers (the values of options like -n 5)."""
    return [w for w in words[1:] if not w.startswith("-") and not re.fullmatch(r"\d+", w)]


def _quote(items: list[str], limit: int = 3) -> str:
    shown = items[:limit] + ([f"{len(items) - limit} more"] if len(items) > limit else [])
    return shown[0] if len(shown) == 1 else ", ".join(shown[:-1]) + " and " + shown[-1]


def _unwrap(words: list[str]) -> list[str]:
    """Drop time, nice, timeout, and xargs with their options, to reach the program they run."""
    while words and PurePosixPath(words[0]).name in WRAPPERS:
        name, words = PurePosixPath(words[0]).name, words[1:]
        while words and words[0].startswith("-"):
            flag, words = words[0], words[1:]
            if name in ("nice", "xargs") and flag in ("-n", "-I", "-L", "-P", "-d") and words:
                words = words[1:]
        if name == "timeout" and words and re.fullmatch(r"[\d.]+[smh]?", words[0]):
            words = words[1:]
    return words


def _one(simple: _Simple) -> tuple[str, Decision]:
    """The class of one simple command (read, change, run, unlisted) and its decision."""
    words = list(simple.words)
    if words and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0]):
        return "refuse", Decision(REFUSE, "I don't set variables in front of a command. " + TOO_COMPLEX)
    fed = any(PurePosixPath(w).name == "xargs" for w in words[:3])
    words = _unwrap(words)
    if not words:
        return "refuse", Decision(REFUSE, "There is no command.")
    prog_path = words[0]
    if "$" in prog_path:
        return "refuse", Decision(REFUSE, "The program cannot be a variable. " + TOO_COMPLEX)
    if prog_path in SHELL_WORDS or prog_path.endswith("()"):
        return "refuse", Decision(REFUSE, "I don't run loops, tests, or functions. " + TOO_COMPLEX)
    prog = PurePosixPath(prog_path).name
    if "/" in prog_path and not prog_path.startswith(("/bin/", "/usr/bin/", "/usr/local/bin/", "/opt/homebrew/bin/")):
        if problem := _path_problem(prog_path):
            return "refuse", problem
    args = _args(words)

    for path in [*args, *simple.reads, *(t for _, t in simple.writes)]:
        if problem := _path_problem(path):
            return "refuse", problem
    if prog in SYSTEM:
        return "refuse", Decision(REFUSE, f"Blocked: {SYSTEM[prog]}.", programs=(prog,), severity=CRITICAL)
    if prog in DESKTOP:
        return "refuse", Decision(REFUSE, NO_DESKTOP, programs=(prog,))
    if prog in NETWORK:
        return "refuse", Decision(REFUSE, NO_FETCH, programs=(prog,))
    if prog in INSTALLERS or (args and args[0] in INSTALL_VERBS.get(prog, ())) or (
        prog.startswith("python") and "-m" in words and words[words.index("-m") + 1 : words.index("-m") + 2] == ["pip"]
    ):
        return "refuse", Decision(REFUSE, NO_NETWORK, programs=(prog,))
    if prog == "git" and args and args[0] in GIT_NETWORK:
        return "refuse", Decision(REFUSE, "The sandbox has no network.", programs=(prog,))
    if prog in ("sh", "bash", "zsh") and any(w.startswith("-") and "c" in w for w in words[1:]):
        return "refuse", Decision(REFUSE, "I run commands directly, not as text passed to another shell.", programs=(prog,))
    if prog == "find":
        for flag in ("-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fls", "-delete"):
            if flag in words:
                return "refuse", Decision(REFUSE, "find can only list files here. Run the other command on its own.")
    if prog in ("awk", "gawk") and any("system" in w or "getline" in w or "|" in w for w in words[1:]):
        return "refuse", Decision(REFUSE, "awk can only read here, with no commands inside it.")

    if fed and prog not in READS:
        return "refuse", Decision(REFUSE, "xargs only feeds reading programs here, because I can't read out its input.")
    cls = "read" if prog in READS else "change" if prog in CHANGES else "run" if prog in RUNS else "unlisted"
    if prog == "sed":
        cls = "change" if any(w.startswith("-i") or w == "--in-place" for w in words[1:]) else "read"
    if prog == "git":
        cls = "read" if args and args[0] in GIT_READS else "change"
    if any(t != "/dev/null" for _, t in simple.writes) and cls == "read":
        cls = "change"
    deletes = tuple(args) if prog in ("rm", "rmdir") else ()
    return cls, Decision(ASK if cls != "read" else RUN, words=_describe(prog, words, args, simple),
                         programs=(prog,), deletes=deletes)  # fmt: skip


def _describe(prog: str, words: list[str], args: list[str], simple: _Simple) -> str:
    """What one simple command does, in words a listener can follow."""
    recursive = any(w.startswith("-") and not w.startswith("--") and ("r" in w or "R" in w) for w in words[1:])
    first = args[0] if args else ""
    if prog == "rm":
        what = _quote(args) if args else "nothing"
        text = f"delete {what}" + (" and everything inside" if recursive else "")
    elif prog == "rmdir":
        text = f"delete the empty folder {_quote(args)}"
    elif prog == "mkdir":
        text = f"make the folder {_quote(args)}"
    elif prog == "touch":
        text = f"create {_quote(args)}"
    elif prog in ("cp", "mv") and len(args) >= 2:
        verb = "copy" if prog == "cp" else "move"
        text = f"{verb} {_quote(args[:-1])} to {args[-1]}"
    elif prog == "chmod":
        text = f"change who can run or edit {_quote(args[1:])}" if len(args) > 1 else "change permissions"
    elif prog == "ln":
        text = f"link {args[-1]} to {args[0]}" if len(args) >= 2 else "make a link"
    elif prog in ("zip", "tar", "gzip"):
        text = f"pack {_quote(args[1:] or args)}"
    elif prog in ("unzip", "gunzip"):
        text = f"unpack {_quote(args)}"
    elif prog == "sed":
        text = f"edit {_quote(args[1:] or args)} in place"
    elif prog == "tee":
        text = f"write the output to {_quote(args)}"
    elif prog in ("python", "python3") and "-m" in words:
        i = words.index("-m")
        module = words[i + 1] if i + 1 < len(words) else ""
        text = "run the tests" if module == "pytest" else f"run the Python module {module}"
    elif prog in FRIENDLY and first:
        text = f"run {first} with {FRIENDLY[prog]}"
    elif prog == "pytest":
        text = "run the tests" + (f" in {_quote(args)}" if args else "")
    elif prog == "make":
        text = f"run make {first}".strip() if first else "run make"
    elif prog == "npm" and first in ("run", "test", "start"):
        text = f"run the npm script {args[1]}" if first == "run" and len(args) > 1 else f"run npm {first}"
    elif prog == "npx":
        text = f"run {first} with npx" if first else "run npx"
    elif prog in ("gcc", "cc", "clang", "c++", "g++", "rustc", "javac", "tsc", "go", "cargo"):
        text = f"build {_quote(args)} with {prog}" if args else f"run {prog}"
    elif prog == "git":
        text = f"show git {first}" if first in GIT_READS else f"change the git repository with git {first}".strip()
    elif prog == "uv" and first == "run":
        text = f"run {_quote(args[1:])} with uv" if len(args) > 1 else "run uv"
    elif prog in READS:
        text = {"ls": "list files", "grep": f"search {_quote(args[1:])} for {first}" if len(args) > 1 else f"search for {first}",
                "rg": f"search for {first}",
                "find": "find files", "wc": "count lines", "cat": f"read {_quote(args)}" if args else "read the input",
                "head": f"read the start of {_quote(args)}" if args else "read the start",
                "tail": f"read the end of {_quote(args)}" if args else "read the end",
                "echo": "print some text", "printf": "print some text"}.get(prog, f"run {prog}")  # fmt: skip
    else:
        text = f"run {prog}" + (f" on {_quote(args)}" if args else "")
    for op, target in simple.writes:
        if target == "/dev/null":
            continue
        text += f", {'adding' if op.endswith('>>') else 'writing'} the output to {target}"
    return text


def check(command: str) -> Decision:
    """Run, ask, or refuse one command line, with the words for a read-back."""
    try:
        parts = split(command)
    except Unreadable as e:
        return Decision(REFUSE, str(e))
    decisions = []
    for simple, _ in parts:
        cls, decision = _one(simple)
        if decision.verdict == REFUSE:
            return decision
        decisions.append((cls, decision))
    joiners = {"&&": ", then ", ";": ", then ", "||": ", or if that fails, ", "|": ", passing the output to "}
    joined = decisions[0][1].words
    for (_, decision), (_, op) in zip(decisions[1:], parts):
        joined += joiners[op] + decision.words
    programs = tuple(dict.fromkeys(p for _, d in decisions for p in d.programs))
    deletes = tuple(p for _, d in decisions for p in d.deletes)
    unlisted = [d.programs[0] for c, d in decisions if c == "unlisted"]
    if all(c == "read" for c, _ in decisions):
        return Decision(RUN, words=joined, programs=programs)
    reason = f"{_quote(unlisted)} {'is' if len(unlisted) == 1 else 'are'} not on my list of known programs." if unlisted else ""
    return Decision(ASK, reason=reason, words=joined, programs=programs, deletes=deletes, unlisted=tuple(unlisted))
