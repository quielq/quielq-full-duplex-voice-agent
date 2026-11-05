"""Workspace MCP server: files, shell, and long-running services in one folder (ADR-021).

Files are real and stay inside $ME3_DATA_DIR/workspace; reads may also use the documents folder.
``bash``, ``serve``, and ``open_in_browser`` are mocks unless ME3_WORKSPACE_LIVE=1, and
``send_file`` is always a mock. ``open_in_browser`` is the one thing that leaves the sandbox
(ADR-026): it runs no command text, only the system opener on one HTML file. Live commands pass servers/command_policy.py (ADR-034) and then run in servers/sandbox.py (ADR-024):
with no sandbox on the machine, nothing runs.
"""

from __future__ import annotations

import atexit
import contextlib
import fnmatch
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path, PurePath
from typing import Annotated, Literal

from fastmcp import FastMCP
from pydantic import Field

from servers.common import OutsideRoots, ToolCallLog, data_dir, documents_dir, inside, serve as serve_mcp, workspace_dir
from servers.documents import extract_text
from servers.sandbox import SAFE_PATH, NoSandbox, wrap
from servers.command_policy import REFUSE, check

mcp = FastMCP("workspace", instructions="Read, write, and run things in the agent's workspace folder.")
mcp.add_middleware(ToolCallLog("workspace"))

MAX_CHARS = 4000
BASH_TIMEOUT_S = 25   # under the policy timeout, so the server reports it and kills the command
MAX_SCAN_BYTES = 2_000_000
MAX_SEND_BYTES = 10_000_000
ENV_ALLOWED = ("LANG", "LC_ALL", "TERM")
BROWSER_FILES = (".html", ".htm")
BROWSER_ENV = ("HOME", "USER", "LANG", "DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")
OPENER_TIMEOUT_S = 8   # under the policy timeout


def _live() -> bool:
    return os.environ.get("ME3_WORKSPACE_LIVE") == "1"


def _readable() -> list[Path]:
    return [workspace_dir(), documents_dir()]


def _command_env() -> dict[str, str]:
    """Only what a shell needs, so no secret in the server's environment reaches a command."""
    env = {k: os.environ[k] for k in ENV_ALLOWED if k in os.environ}
    tmp = workspace_dir() / ".tmp"
    tmp.mkdir(exist_ok=True)
    return env | {"HOME": str(workspace_dir()), "PATH": SAFE_PATH, "TMPDIR": str(tmp)}


def _sandboxed(command: str, network: str, cwd: Path | None = None) -> list[str]:
    """The argv for a live command. Refuses by raising, so no path runs one unchecked."""
    decision = check(command)
    if decision.verdict == REFUSE:
        raise NoSandbox(decision.reason)
    return wrap(["/bin/bash", "-c", command], workspace_dir(), [documents_dir()], _command_env(), network, cwd)


def _mock(tool: str, **fields) -> dict:
    with open(data_dir() / "workspace_mock.jsonl", "a") as f:
        f.write(json.dumps({"ts": time.time(), "tool": tool, **fields}) + "\n")
    return {"mock": True, "ran": False, "note": "Nothing ran. This tool is a mock until it is switched on."}


def _clip(text: str) -> str:
    return text if len(text) <= MAX_CHARS else text[-MAX_CHARS:]  # the end of output is what matters


def _keep_deleted(command: str, folder: Path) -> list[str]:
    """Copy what a command's rm or rmdir would delete into .versions first (ADR-034). Globs are
    matched the way the shell would, from the command's folder, and only inside the workspace."""
    root, kept = workspace_dir().resolve(), []
    for pattern in check(command).deletes:
        for match in sorted(folder.glob(pattern)) if any(c in pattern for c in "*?[") else [folder / pattern]:
            if match.is_symlink():  # rm removes the link, not what it points at
                continue
            if match.name.startswith(".") and not PurePath(pattern).name.startswith("."):
                continue  # the shell's * skips hidden names, so rm does too
            try:
                target = inside(str(match), [root])
            except OutsideRoots:
                continue
            if not target.exists() or target == root or ".versions" in target.relative_to(root).parts:
                continue
            copy = root / ".versions" / f"{time.time_ns()}-{target.name}"
            copy.parent.mkdir(exist_ok=True)
            if target.is_dir():
                shutil.copytree(target, copy, symlinks=True)
            else:
                shutil.copy2(target, copy)
            kept.append(str(copy.relative_to(root)))
    return kept


def _keep_version(target: Path) -> str | None:
    """Copy a file about to change, since writes run with no undo window (ADR-028)."""
    if not target.is_file():
        return None
    folder = workspace_dir() / ".versions"
    folder.mkdir(exist_ok=True)
    copy = folder / f"{time.time_ns()}-{target.name}"
    shutil.copyfile(target, copy)
    return str(copy.relative_to(workspace_dir()))


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
def read_file(
    path: Annotated[str, Field(min_length=1, description="File path, relative to the workspace")],
    max_chars: Annotated[int, Field(ge=1, le=20000)] = MAX_CHARS,
) -> dict:
    """Read a text, PDF, DOCX, or XLSX file."""
    try:
        target = inside(path, _readable())
    except OutsideRoots as e:
        return {"error": str(e)}
    if not target.is_file():
        return {"error": f"no file at {path}"}
    text = extract_text(target)
    return {"path": path, "text": text[:max_chars], "truncated": len(text) > max_chars}


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
def list_files(
    file_pattern: Annotated[str, Field(description="Only these file names, for example *.pdf")] = "*",
    max_results: Annotated[int, Field(ge=1, le=100)] = 20,
) -> dict:
    """List the files in the workspace and documents folders, newest first. To say what is in a folder, use the summaries: they count every file, and name each top-level folder and file."""
    found: list[tuple[float, int, str]] = []
    summaries: dict[str, dict] = {}
    for root in _readable():
        summary = summaries.setdefault("workspace" if root == workspace_dir() else "documents",
                                       {"folders": {}, "files": [], "total_files": 0})  # fmt: skip
        for p in root.rglob("*"):
            hidden = any(s.startswith(".") for s in p.relative_to(root).parts)
            if hidden or p.is_symlink() or not p.is_file() or not fnmatch.fnmatch(p.name, file_pattern):
                continue
            stat = p.stat()
            top = p.relative_to(root).parts
            summary["total_files"] += 1
            if len(top) > 1:
                summary["folders"][top[0]] = summary["folders"].get(top[0], 0) + 1
            else:
                summary["files"].append(top[0])
            # A relative path starts from the workspace, so read_file needs documents in full.
            name = str(p.relative_to(root)) if root == workspace_dir() else str(p)
            found.append((stat.st_mtime, stat.st_size, name))
    found.sort(reverse=True)
    files = [{"path": name, "bytes": size, "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))}
             for mtime, size, name in found[:max_results]]  # fmt: skip
    for summary in summaries.values():
        summary["folders"] = dict(sorted(summary["folders"].items()))
        summary["files"].sort()
    return {"files": files, "truncated": len(found) > max_results, "workspace_folder": str(workspace_dir()),
            "documents_folder": str(documents_dir()),
            "workspace_summary": summaries.get("workspace"), "documents_summary": summaries.get("documents")}  # fmt: skip


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False}
)
def write_file(
    path: Annotated[str, Field(min_length=1, description="File path, relative to the workspace")],
    content: str,
    mode: Annotated[Literal["write", "append"], Field(description="write replaces the file")] = "write",
) -> dict:
    """Write a file in the workspace, creating folders as needed. Use append to add to a long file. The documents folder is read-only."""
    try:
        target = inside(path, [workspace_dir()])
    except OutsideRoots as e:
        return {"error": str(e)}
    target.parent.mkdir(parents=True, exist_ok=True)
    previous = _keep_version(target) if mode == "write" else None
    with open(target, "w" if mode == "write" else "a") as f:
        f.write(content)
    result = {"path": path, "full_path": str(target), "bytes": target.stat().st_size}
    return result | {"previous_version": previous} if previous else result


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False}
)
def edit_file(
    path: Annotated[str, Field(min_length=1, description="File path, relative to the workspace")],
    old_string: Annotated[str, Field(min_length=1, description="Exact text to replace")],
    new_string: str,
    replace_all: bool = False,
) -> dict:
    """Replace exact text in a workspace file. The text must occur once unless replace_all is set."""
    try:
        target = inside(path, [workspace_dir()])
    except OutsideRoots as e:
        return {"error": str(e)}
    if not target.is_file():
        return {"error": f"no file at {path}"}
    text = target.read_text()
    count = text.count(old_string)
    if not count:
        return {"error": "that text is not in the file"}
    if count > 1 and not replace_all:
        return {"error": f"that text occurs {count} times, so set replace_all or give more of it"}
    previous = _keep_version(target)
    target.write_text(text.replace(old_string, new_string))
    return {"path": path, "replaced": count, "previous_version": previous}


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
def grep(
    pattern: Annotated[str, Field(min_length=1, description="Regular expression")],
    path: Annotated[str, Field(description="Folder or file, relative to the workspace")] = ".",
    file_pattern: Annotated[str, Field(description="Only these file names, for example *.md")] = "*",
    case_sensitive: bool = False,
    max_results: Annotated[int, Field(ge=1, le=100)] = 20,
) -> dict:
    """Search file contents with a regular expression."""
    try:
        root = inside(path, _readable())
        rule = re.compile(pattern, 0 if case_sensitive else re.I)
    except OutsideRoots as e:
        return {"error": str(e)}
    except re.error as e:
        return {"error": f"bad pattern: {e}"}
    hits: list[dict] = []
    for p in [root] if root.is_file() else sorted(root.rglob("*")):
        hidden = p != root and any(s.startswith(".") for s in p.relative_to(root).parts)
        if hidden or not p.is_file() or not fnmatch.fnmatch(p.name, file_pattern):
            continue
        if p.stat().st_size > MAX_SCAN_BYTES:
            continue
        data = p.read_bytes()
        if b"\0" in data:
            continue
        for n, line in enumerate(data.decode(errors="replace").splitlines(), 1):
            if rule.search(line):
                hits.append({"file": str(p.relative_to(root)) if p != root else p.name,
                             "line": n, "text": line.strip()[:300]})  # fmt: skip
                if len(hits) >= max_results:
                    return {"matches": hits, "truncated": True}
    return {"matches": hits, "truncated": False}


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True}
)
def bash(
    command: Annotated[str, Field(min_length=1, description="The shell command")],
    cwd: Annotated[str, Field(description="Folder, relative to the workspace")] = ".",
) -> dict:
    """Run a short shell command in the workspace. Use list_files to see files, and serve for anything over about 20 seconds. Reads run at once. A change, a delete, or running code is first read back to the user, who must say yes, and rm and rmdir keep a copy of what they delete in .versions, so call this with rm when asked to delete files or folders."""
    try:
        folder = inside(cwd, [workspace_dir()])
    except OutsideRoots as e:
        return {"error": str(e)}
    if not _live():
        return _mock("bash", command=command, cwd=cwd)
    try:
        argv = _sandboxed(command, "none", folder)
    except NoSandbox as e:
        return {"error": str(e)}
    kept = _keep_deleted(command, folder)
    proc = subprocess.Popen(
        argv, cwd=folder, env=_command_env(), text=True, errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
    )  # fmt: skip
    try:
        out, err = proc.communicate(timeout=BASH_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        out, err = proc.communicate()
        return {"error": f"stopped after {BASH_TIMEOUT_S} s, use serve for long work", "stdout": _clip(out)}
    result = {"returncode": proc.returncode, "stdout": _clip(out), "stderr": _clip(err)}
    return result | {"kept_copies": kept} if kept else result


def _services() -> dict:
    p = data_dir() / "services.json"
    return json.loads(p.read_text()) if p.exists() else {}


def _save_services(services: dict) -> None:
    (data_dir() / "services.json").write_text(json.dumps(services))


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        with contextlib.suppress(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)  # our own child: an exited one must not look alive
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _stop(name: str, services: dict) -> None:
    pid = services.pop(name)["pid"]
    if _alive(pid):
        os.killpg(pid, signal.SIGTERM)


def _start(name: str, command: str) -> dict:
    root = workspace_dir()
    log = root / ".services" / f"{name}.log"
    log.parent.mkdir(exist_ok=True)
    with open(log, "ab") as f:
        proc = subprocess.Popen(_sandboxed(command, "local"), cwd=root, env=_command_env(), stdout=f,
                                stderr=subprocess.STDOUT, start_new_session=True)  # fmt: skip
    return {"pid": proc.pid, "command": command, "log": str(log), "started_at": time.time()}


def _stop_all() -> None:
    """Nothing keeps running after the agent has gone."""
    if _live():
        services = _services()
        for name in list(services):
            _stop(name, services)
        _save_services(services)


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True}
)
def serve(
    action: Literal["start", "stop", "restart"],
    name: Annotated[str, Field(pattern=r"^[\w-]{1,40}$", description="A short name for the service")],
    command: Annotated[str | None, Field(description="The command to run, for start")] = None,
) -> dict:
    """Start, stop, or restart a background job or web server in the workspace."""
    if not _live():
        return _mock("serve", action=action, name=name, command=command)
    services = _services()
    if action == "start" and not command:
        return {"error": "start needs a command"}
    if action != "start" and name not in services:
        return {"error": f"no service called {name}"}
    if action == "start" and name in services and _alive(services[name]["pid"]):
        return {"error": f"{name} is already running"}
    command = command or services.get(name, {}).get("command")
    if action != "stop":
        try:
            _sandboxed(command, "local")
        except NoSandbox as e:  # refused before the old one stops
            return {"error": str(e)}
    if name in services:
        _stop(name, services)
    if action != "stop":
        services[name] = _start(name, command)
    _save_services(services)
    return {"name": name, "action": action, "pid": services.get(name, {}).get("pid")}


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
def serve_status(
    name: Annotated[str | None, Field(description="One service, to include its recent log")] = None,
    lines: Annotated[int, Field(ge=1, le=200)] = 20,
) -> dict:
    """List background services, or show one service's status and recent log."""
    services = _services()
    if name is None:
        return {"services": [{"name": n, "running": _alive(s["pid"])} for n, s in services.items()]}
    if name not in services:
        return {"error": f"no service called {name}"}
    log = Path(services[name]["log"])
    tail = log.read_text(errors="replace").splitlines()[-lines:] if log.exists() else []
    return {"name": name, "running": _alive(services[name]["pid"]), "log": _clip("\n".join(tail))}


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True}
)
def send_file(path: Annotated[str, Field(min_length=1, description="File path, relative to the workspace")]) -> dict:
    """Send a file to the user. Mock: it is copied to an outbox folder and goes nowhere else."""
    try:
        source = inside(path, _readable())
    except OutsideRoots as e:
        return {"error": str(e)}
    if not source.is_file():
        return {"error": f"no file at {path}"}
    if source.stat().st_size > MAX_SEND_BYTES:
        return {"error": "the file is over 10 MB"}
    outbox = data_dir() / "send_outbox"
    outbox.mkdir(exist_ok=True)
    copy = outbox / f"{uuid.uuid4().hex[:8]}-{source.name}"
    shutil.copyfile(source, copy)
    return {"mock": True, "sent": False, "saved_as": copy.name, "note": "Saved to the outbox. Nothing left this machine."}


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True}
)
def open_in_browser(
    path: Annotated[str, Field(min_length=1, description="HTML file, relative to the workspace")],
) -> dict:
    """Open an HTML file from the workspace in the user's web browser."""
    try:
        target = inside(path, [workspace_dir()])
    except OutsideRoots as e:
        return {"error": str(e)}
    if target.suffix.lower() not in BROWSER_FILES:
        return {"error": "only HTML files can be opened in the browser"}
    if not target.is_file():
        return {"error": f"no file at {path}"}
    if not _live():
        return _mock("open_in_browser", path=path)
    opener = shutil.which("open" if sys.platform == "darwin" else "xdg-open", path=SAFE_PATH)
    if not opener:
        return {"error": "this machine has no way to open a browser"}
    env = {k: os.environ[k] for k in BROWSER_ENV if k in os.environ} | {"PATH": SAFE_PATH}
    try:
        subprocess.run([opener, str(target)], env=env, check=True, capture_output=True, timeout=OPENER_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return {"error": "the browser could not be opened"}
    return {"opened": target.name}


if __name__ == "__main__":
    atexit.register(_stop_all)
    serve_mcp(mcp)
