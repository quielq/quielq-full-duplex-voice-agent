"""The command policy (ADR-034) and the OS sandbox (ADR-024). The sandbox cases run real commands on this
machine. Bubblewrap cannot run on a Mac, so the Pi arguments are only checked for shape here."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from servers import sandbox
from servers.command_policy import ASK, CRITICAL, REFUSE, RUN, check
from servers.tasks.workspace import server as workspace

darwin_only = pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec is on the Mac")


@pytest.mark.parametrize("command", [
    "ls -la", "cat a.txt | wc -l", "grep -rn security src", "head -n 5 a.txt 2>&1", "git status",
    "find . -name '*.py'", "ls | xargs wc -l", "echo hi 2>/dev/null", "ls;",
])  # fmt: skip
def test_reads_run_at_once(command):
    assert check(command).verdict == RUN


@pytest.mark.parametrize(("command", "words"), [
    ("python3 notes.py", "run notes.py with Python"),
    ("rm -rf build", "delete build and everything inside"),
    ("mkdir -p site/css && cp a.html site/", "make the folder site/css, then copy a.html to site/"),
    ("echo hi > out.txt", "print some text, writing the output to out.txt"),
    ("sed -i s/a/b/ x.txt", "edit x.txt in place"),
    ("python3 -m http.server 8000", "run the Python module http.server"),
    ("npm run build || make", "run the npm script build, or if that fails, run make"),
])  # fmt: skip
def test_changes_and_runs_ask_in_plain_words(command, words):
    decision = check(command)
    assert decision.verdict == ASK and decision.words == words


def test_rm_names_what_to_keep():
    assert check("rm -r old a.txt && ls").deletes == ("old", "a.txt")


def test_the_model_is_told_a_delete_asks_and_keeps_a_copy():
    # Live on Nov 4, Haiku refused to call bash for a delete because it thought it was permanent,
    # so the read-back never ran. What the model is told must match what the policy does.
    from harness.bot import SYSTEM_PROMPT

    decision = check("rm -r delegate_trial")
    assert decision.verdict == ASK and decision.deletes
    assert "keeps a copy" in SYSTEM_PROMPT and "Do not refuse" in SYSTEM_PROMPT
    doc = workspace.bash.__doc__ or getattr(workspace.bash, "description", "")
    assert "must say yes" in doc and ".versions" in doc


@pytest.mark.parametrize("command", [
    "sudo ls", "ls; sudo ls", "rm -rf /", "rm -rf ~", "rm -rf ~/", "cat .env", "cat ../../.env",
    "cat ~/.ssh/id_rsa", "shutdown now", "mkfs /dev/sda", "dd if=/dev/zero of=x", "launchctl list",
    "crontab -l", "defaults write x y z", "security find-generic-password -a x", "docker run x",
    "echo x > /dev/sda", "printenv",
])  # fmt: skip
def test_dangerous_commands_are_critical(command):
    decision = check(command)
    assert decision.verdict == REFUSE and decision.severity == CRITICAL


@pytest.mark.parametrize("command", [
    "$CMD", "echo $(id)", "echo `id`", "diff <(ls) b", "case x in y) ls;; esac", "f() { ls; }; f",
    "for i in 1 2; do ls; done", "sleep 9 &", "FOO=1 ls", "cat <<EOF", "bash -c 'ls'", "ls &&",
    "echo 'open", "find . -exec rm {} +", "find . -delete", "xargs rm < list.txt", "awk '{system(\"id\")}'",
])  # fmt: skip
def test_what_is_not_simple_is_refused(command):
    decision = check(command)
    assert decision.verdict == REFUSE and decision.severity != CRITICAL


@pytest.mark.parametrize("command", ["pip install requests", "npm install", "uv add x", "python3 -m pip install x", "brew install x"])
def test_installs_are_refused_clearly(command):
    decision = check(command)
    assert decision.verdict == REFUSE and "installing packages is off" in decision.reason


@pytest.mark.parametrize("command", ["curl https://example.com", "wget x", "git push", "ssh a@b"])
def test_network_tools_are_refused(command):
    assert "no network" in check(command).reason


@pytest.mark.parametrize("command", ["cat /etc/passwd", "ls ../..", "cp a.txt /tmp/a", "cat ~/notes"])
def test_paths_stay_in_the_workspace(command):
    decision = check(command)
    assert decision.verdict == REFUSE and "workspace" in decision.reason


@pytest.mark.parametrize("command", ["open hello.html", "/usr/bin/open a.html", "xdg-open a.html", "osascript -e 1"])
def test_desktop_commands_are_refused_before_anyone_is_asked(command):
    decision = check(command)
    assert decision.verdict == REFUSE and decision.severity != CRITICAL and "desktop" in decision.reason


@pytest.mark.parametrize("command", ["cowsay hi", "ls | cowsay"])
def test_an_unlisted_program_asks_and_is_named(command):
    decision = check(command)
    assert decision.verdict == ASK and decision.unlisted == ("cowsay",)


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setenv("ME3_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ME3_WORKSPACE_LIVE", "1")
    return tmp_path


def run(command):
    return workspace.bash(command)


@darwin_only
def test_the_server_refuses_a_critical_command_itself(live):
    assert "Blocked" in run("cat .env")["error"]


@darwin_only
def test_commands_work_in_the_workspace(live):
    out = run("echo hi > a.txt && cat a.txt && python3 -c 'print(2)'")
    assert out["returncode"] == 0 and out["stdout"] == "hi\n2\n"


@darwin_only
def test_a_file_outside_the_workspace_cannot_be_read(live):
    secret = live / "secret.txt"
    secret.write_text("no")
    assert "workspace" in run(f"cat {secret}")["error"]  # the policy refuses it first
    out = run(f"python3 -c \"print(open('{secret}').read())\"")  # so ask the sandbox
    assert out["returncode"] != 0 and "no\n" not in out["stdout"]


@darwin_only
def test_a_symlink_cannot_leave_the_workspace(live):
    secret = live / "secret.txt"
    secret.write_text("topsecret")
    (workspace.workspace_dir() / "link").symlink_to(secret)
    out = run("cat link")
    assert out["returncode"] != 0 and "topsecret" not in out["stdout"]


@darwin_only
def test_an_interpreter_cannot_read_home(live):
    out = run(f"python3 -c \"print(len(list(__import__('os').listdir('{Path.home()}'))))\"")
    assert out["returncode"] != 0


@darwin_only
def test_nothing_can_be_written_outside(live):
    assert run(f"python3 -c \"open('{live / 'out.txt'}', 'w')\"")["returncode"] != 0
    assert not (live / "out.txt").exists()


@darwin_only
def test_there_is_no_network(live):
    out = run("python3 -c \"import socket; socket.create_connection(('1.1.1.1', 53), 3)\"")
    assert out["returncode"] != 0 and "not permitted" in out["stderr"]


@darwin_only
def test_a_service_can_listen_on_this_machine_only(live):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert "error" not in workspace.serve("start", "web", f"python3 -m http.server {port} --bind 127.0.0.1")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    else:
        pytest.fail(workspace.serve_status("web").get("log", "no log"))
    workspace.serve("stop", "web")


@darwin_only
def test_no_read_only_folders_does_not_open_every_read(tmp_path):
    secret = tmp_path / "secret"
    secret.write_text("no")
    (tmp_path / "work").mkdir()
    argv = sandbox.wrap(["/bin/cat", str(secret)], tmp_path / "work", [], {})
    assert subprocess.run(argv, capture_output=True).returncode != 0


def test_no_sandbox_means_nothing_runs(live, monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)
    assert "no sandbox" in run("touch ran.txt")["error"]
    assert "no sandbox" in workspace.serve("start", "web", "sleep 1")["error"]
    assert not (workspace.workspace_dir() / "ran.txt").exists()


def test_a_refused_restart_leaves_the_old_service_alone(live, monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)
    assert "error" in workspace.serve("start", "web", "sleep 1")
    assert workspace.serve_status()["services"] == []


def test_the_pi_arguments_confine_the_command(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox.sys, "platform", "linux")
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/bwrap")
    root, docs = tmp_path / "work", tmp_path / "docs"
    argv = sandbox.wrap(["/bin/bash", "-c", "ls"], root, [docs], {"HOME": str(root)}, cwd=root / "sub")
    assert argv[0] == "/usr/bin/bwrap" and argv[-3:] == ["/bin/bash", "-c", "ls"]
    assert "--unshare-net" in argv and "--clearenv" in argv and "--die-with-parent" in argv
    assert ["--bind", str(root.resolve()), str(root.resolve())] == argv[argv.index("--bind"):argv.index("--bind") + 3]
    assert argv[argv.index("--ro-bind") + 1] == str(docs.resolve())
    assert argv[argv.index("--chdir") + 1] == str(root / "sub")
    assert str(Path.home()) not in argv


def test_a_service_on_the_pi_keeps_the_host_network(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox.sys, "platform", "linux")
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/bwrap")
    assert "--unshare-net" not in sandbox.wrap(["x"], tmp_path, [], {}, "local")


@darwin_only
def test_rm_keeps_a_copy_first(live):
    site = workspace.workspace_dir() / "site"
    site.mkdir()
    (site / "index.html").write_text("<p>hi</p>")
    (workspace.workspace_dir() / "a.tmp").write_text("x")
    out = run("rm -r site && rm *.tmp")
    assert out["returncode"] == 0 and not site.exists()
    kept = [workspace.workspace_dir() / k for k in out["kept_copies"]]
    assert len(kept) == 2 and (kept[0] / "index.html").read_text() == "<p>hi</p>"
    assert kept[1].read_text() == "x"
