"""Run a command inside an OS sandbox (ADR-024): sandbox-exec on the Mac, bubblewrap on the Pi.

This is the real boundary for ``bash`` and ``serve``. A command can write only in the workspace,
read only that, the documents folder, and system files, and has no network. ``serve`` may also
listen on this machine. No sandbox means nothing runs: the caller refuses, it never falls back.

sandbox-exec is deprecated by Apple but still ships in macOS, and we read its profile language
from Apple's system profiles. bwrap needs user namespaces, so bench/doctor.py checks it runs.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Literal

Network = Literal["none", "local"]
SAFE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin"
MAC_SYSTEM_READS = ("/usr", "/bin", "/sbin", "/System", "/Library", "/opt/homebrew", "/private/etc",
                    "/private/var/db/timezone", "/private/var/select", "/Applications/Xcode.app", "/dev")  # fmt: skip
PI_SYSTEM_READS = ("/usr", "/etc/alternatives", "/etc/ld.so.cache", "/etc/ssl", "/etc/localtime")


class NoSandbox(RuntimeError):
    """This machine cannot sandbox a command, so it must not run one."""


def _mac_profile(root: Path, read_only: list[Path], network: Network) -> str:
    def subpaths(paths) -> str:
        return " ".join(f"(subpath {json.dumps(str(p))})" for p in paths)

    rules = [
        "(version 1)",
        "(deny default)",
        "(allow process-exec*)",
        "(allow process-fork)",
        "(allow signal (target same-sandbox))",
        "(allow sysctl-read)",
        '(allow file-read-metadata (literal "/") (subpath "/private") (subpath "/var") (subpath "/etc"))',
        '(allow file-read* (literal "/") (literal "/etc") (literal "/tmp") (literal "/var"))',
        f"(allow file-read* {subpaths(MAC_SYSTEM_READS)})",
        '(allow file-write* (literal "/dev/null") (literal "/dev/zero") (literal "/dev/dtracehelper") (regex #"^/dev/tty"))',
        f"(allow file-read* file-write* {subpaths([root])})",
    ]
    if read_only:  # an empty allow rule has no filter, and would allow every read
        rules.append(f"(allow file-read* {subpaths(read_only)})")
    for parent in root.parents:  # a shell must be able to stat its way to its own folder
        rules.append(f"(allow file-read-metadata (literal {json.dumps(str(parent))}))")
    if network == "local":
        rules += ['(allow network-bind (local ip "localhost:*"))',
                  '(allow network-inbound (local ip "localhost:*"))',
                  '(allow network-outbound (remote ip "localhost:*"))']  # fmt: skip
    return "\n".join(rules)


def _pi_args(root: Path, read_only: list[Path], network: Network) -> list[str]:
    args = ["--die-with-parent", "--new-session", "--unshare-user", "--unshare-pid", "--unshare-ipc",
            "--unshare-uts", "--unshare-cgroup", "--clearenv", "--proc", "/proc", "--dev", "/dev",
            "--tmpfs", "/tmp", "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib",
            "--symlink", "usr/sbin", "/sbin"]  # fmt: skip
    if Path("/usr/lib64").exists():
        args += ["--symlink", "usr/lib64", "/lib64"]
    for path in PI_SYSTEM_READS:
        args += ["--ro-bind-try", path, path]
    for path in read_only:
        args += ["--ro-bind", str(path), str(path)]
    args += ["--bind", str(root), str(root)]
    if network == "none":
        args.append("--unshare-net")
    return args  # "local" shares the host network: a bound port must be reachable from outside


def wrap(argv: list[str], root: Path, read_only: list[Path], env: dict[str, str], network: Network = "none",
         cwd: Path | None = None) -> list[str]:
    """``argv`` inside the sandbox. Raises NoSandbox when this machine has none."""
    root = root.resolve()
    read_only = [p.resolve() for p in read_only]
    if sys.platform == "darwin" and (tool := shutil.which("sandbox-exec")):
        return [tool, "-p", _mac_profile(root, read_only, network), *argv]
    if sys.platform == "linux" and (tool := shutil.which("bwrap")):
        pairs = [x for k, v in env.items() for x in ("--setenv", k, v)]
        return [tool, *_pi_args(root, read_only, network), *pairs, "--chdir", str(cwd or root), *argv]
    raise NoSandbox("no sandbox on this machine, install bubblewrap")
