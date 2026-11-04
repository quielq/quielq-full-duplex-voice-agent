"""The delegate server and its worker (ADR-035). Fakes only: no network, no spend."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from bench.cost_ledger import CostLedger
from harness.delegate_watch import DelegateWatch, finished_phrase
from harness.mcp_tools import TASK_SERVERS, _server_env
from servers.tasks.delegate import server, worker
from servers.tasks.delegate.worker import AnthropicModel, Call, FakeModel, needs_yes, run_task

PRICES = {"input_per_token": 0.0000001, "output_per_token": 0.0000005}


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("ME3_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ME3_WORKSPACE_LIVE", raising=False)
    monkeypatch.delenv("ME3_TOOL_STAGES", raising=False)
    monkeypatch.setattr(server, "_tasks", {})
    monkeypatch.setattr(server, "_jobs", {})
    monkeypatch.setattr(server, "_loaded", False)
    return tmp_path


def run(coro):
    return asyncio.run(coro)


class FakeClient:
    """Stands in for AsyncAnthropic: scripted responses with usage, and a count of calls."""

    def __init__(self, responses, input_tokens=1000, output_tokens=200):
        self._responses = list(responses)
        self.calls = 0
        self.messages = SimpleNamespace(create=self._create)
        self._usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)

    async def _create(self, **kwargs):
        assert kwargs["model"] == "claude-haiku-5-5"
        content = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        stop = "tool_use" if any(b["type"] == "tool_use" for b in content) else "end_turn"
        return SimpleNamespace(content=content, usage=self._usage, stop_reason=stop)


def _model(tmp_path, client, cap=5.0):
    books = CostLedger(tmp_path / "ledger.jsonl", {"anthropic": cap})
    return AnthropicModel(books, "claude-haiku-5-5", 1024, PRICES, client=client), books


LIST = [{"type": "tool_use", "id": "u1", "name": "list_files", "input": {}}]
DONE = [{"type": "text", "text": "All done."}]


# Caps


def test_a_zero_cap_refuses_before_any_model_call(tmp_path):
    client = FakeClient([DONE])
    model, books = _model(tmp_path, client, cap=0.0)
    out = run(run_task("list my files", model))
    assert out.state == worker.FAILED and "cap is zero" in out.result
    assert client.calls == 0 and books.spent_today("anthropic") == 0


def test_start_task_refuses_at_once_on_a_zero_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("ME3_TOOL_STAGES", "delegate=anthropic")
    monkeypatch.setattr(server, "ledger", lambda: CostLedger(tmp_path / "l.jsonl", {"anthropic": 0.0}))
    result = run(server.start_task("build a page"))
    assert "cap is zero" in result["error"] and server._tasks == {}


def test_every_model_call_is_recorded_under_anthropic(tmp_path):
    model, books = _model(tmp_path, FakeClient([LIST, DONE]))
    out = run(run_task("list my files", model))
    assert out.state == worker.DONE and out.result == "All done." and out.steps == 2
    entries = [json.loads(line) for line in (tmp_path / "ledger.jsonl").read_text().splitlines()]
    assert {e["provider"] for e in entries} == {"anthropic"}
    assert {e["kind"] for e in entries} == {"llm_input_tokens", "llm_output_tokens"}
    assert books.spent_today("anthropic") == pytest.approx(out.usd) == pytest.approx(2 * (1000e-7 + 200 * 5e-7))


def test_the_per_task_cost_cap_stops_a_task(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "MAX_TASK_USD", 0.01)
    client = FakeClient([LIST], input_tokens=50_000, output_tokens=1000)  # 0.0055 a call
    out = run(run_task("list my files forever", _model(tmp_path, client)[0]))
    assert out.state == worker.STOPPED and "cost cap" in out.result
    assert client.calls == 2


def test_the_daily_cap_stops_a_task_midway(tmp_path):
    client = FakeClient([LIST], input_tokens=50_000, output_tokens=1000)
    out = run(run_task("list my files forever", _model(tmp_path, client, cap=0.01)[0]))
    assert out.state == worker.FAILED and "cap" in out.result and client.calls == 2


def test_the_step_cap_stops_a_loop():
    model = FakeModel([{"tool": "list_files", "args": {}}])  # never finishes
    out = run(run_task("loop", model))
    assert out.state == worker.STOPPED and out.steps == worker.MAX_STEPS == model.calls


# A scripted multi-step task


def test_a_scripted_multi_step_task_writes_and_checks_files(data):
    model = FakeModel([
        {"tool": "write_file", "args": {"path": "site/plan.md", "content": "1. page\n"}},
        [{"tool": "write_file", "args": {"path": "site/index.html", "content": "<h1>Hi</h1>"}},
         {"tool": "edit_file", "args": {"path": "site/index.html", "old_string": "Hi", "new_string": "Hello"}}],
        {"tool": "bash", "args": {"command": "ls site"}},
        {"tool": "grep", "args": {"pattern": "Hello", "path": "site"}},
        {"text": "I wrote a plan and a page that says Hello."},
    ])  # fmt: skip
    steps = []
    out = run(run_task("build a hello page", model, steps.append))
    assert out.state == worker.DONE and "Hello" in out.result
    assert (data / "workspace" / "site" / "index.html").read_text() == "<h1>Hello</h1>"
    assert [s["tool"] for s in steps] == ["write_file", "write_file", "edit_file", "bash", "grep"]
    assert all(s["ran"] for s in steps)


def test_a_tool_error_goes_back_to_the_model():
    seen = []

    class Watching(FakeModel):
        async def reply(self, system, messages, tools):
            seen.append(messages[-1])
            return await super().reply(system, messages, tools)

    model = Watching([{"tool": "read_file", "args": {"path": "../../.env"}}, {"text": "No luck."}])
    out = run(run_task("read the secrets", model))
    assert out.state == worker.DONE
    assert seen[-1]["content"][0]["is_error"] and "outside" in seen[-1]["content"][0]["content"]


# Needs your yes


@pytest.mark.parametrize("call, words", [
    (Call("1", "bash", {"command": "rm -rf site"}), "delete"),
    (Call("1", "bash", {"command": "python3 make_site.py"}), "Python"),
    (Call("1", "serve", {"action": "start", "name": "web", "command": "python3 -m http.server"}), "service web"),
    (Call("1", "open_in_browser", {"path": "site/index.html"}), "open site/index.html in your browser"),
    (Call("1", "send_message", {"to": "Ana", "body": "hi"}), "send message"),
])  # fmt: skip
def test_what_needs_a_yes_is_named_in_plain_words(call, words):
    assert words in needs_yes(call)


@pytest.mark.parametrize("command", ["ls", "cat a.txt | wc -l", "grep -r hello ."])
def test_reads_need_no_yes(command):
    assert needs_yes(Call("1", "bash", {"command": command})) is None


def test_a_task_stops_at_needs_your_yes_and_runs_nothing_in_that_reply(data):
    (data / "workspace" / "site").mkdir(parents=True)
    (data / "workspace" / "site" / "keep.txt").write_text("x")
    model = FakeModel([
        [{"tool": "write_file", "args": {"path": "new.txt", "content": "y"}},
         {"tool": "bash", "args": {"command": "rm -rf site"}}],
        {"text": "unreachable"},
    ])  # fmt: skip
    out = run(run_task("clean up", model))
    assert out.state == worker.NEEDS_YES and out.result.startswith("It needs your yes for: delete")
    assert (data / "workspace" / "site" / "keep.txt").exists()
    assert not (data / "workspace" / "new.txt").exists()
    assert model.calls == 1


def test_a_live_ask_command_still_never_runs(data, monkeypatch):
    monkeypatch.setenv("ME3_WORKSPACE_LIVE", "1")
    ran = []
    monkeypatch.setattr(worker.workspace, "bash", lambda **a: ran.append(a) or {"returncode": 0})
    out = run(run_task("x", FakeModel([{"tool": "bash", "args": {"command": "touch a"}}])))
    assert out.state == worker.NEEDS_YES and ran == []


def test_a_critical_refusal_ends_the_task():
    out = run(run_task("x", FakeModel([{"tool": "bash", "args": {"command": "sudo ls"}}, {"text": "ok"}])))
    assert out.state == worker.FAILED and "unsafe" in out.result


def test_an_ordinary_refusal_is_an_error_the_model_hears():
    model = FakeModel([{"tool": "bash", "args": {"command": "curl https://example.com"}}, {"text": "No network."}])
    out = run(run_task("x", model))
    assert out.state == worker.DONE and out.result == "No network."


# The server


async def _finish(task_id):
    await server._jobs[task_id]


def test_start_returns_at_once_and_the_result_is_kept(data):
    async def go():
        started = await server.start_task("look at my files")
        assert started["state"] == "running"
        task_id = started["task_id"]
        assert server.task_status(task_id)["state"] in ("running", "done")
        await _finish(task_id)
        return task_id

    task_id = run(go())
    result = server.task_result(task_id)
    assert result["state"] == "done" and result["result"] == "I looked through the workspace files."
    saved = json.loads((data / "delegate.json").read_text())
    assert saved[task_id]["state"] == "done" and saved[task_id]["finished_at"]


class SlowModel(FakeModel):
    async def reply(self, system, messages, tools):
        await asyncio.sleep(30)
        return await super().reply(system, messages, tools)


def test_cancel_stops_a_running_task(data, monkeypatch):
    monkeypatch.setattr(server, "make_model", SlowModel)

    async def go():
        task_id = (await server.start_task("a slow job"))["task_id"]
        await asyncio.sleep(0.05)
        return task_id, await server.cancel_task(task_id)

    task_id, cancelled = run(go())
    assert cancelled["state"] == "cancelled"
    assert server.task_result(task_id)["state"] == "cancelled"
    assert server._jobs == {}
    assert json.loads((data / "delegate.json").read_text())[task_id]["state"] == "cancelled"


def test_cancel_after_the_end_changes_nothing():
    async def go():
        task_id = (await server.start_task("look at my files"))["task_id"]
        await _finish(task_id)
        return await server.cancel_task(task_id)

    assert run(go())["state"] == "done"


def test_unknown_ids_and_too_many_tasks(monkeypatch):
    monkeypatch.setattr(server, "make_model", SlowModel)

    async def go():
        for _ in range(server.MAX_RUNNING):
            await server.start_task("a slow job")
        refused = await server.start_task("one more")
        for job in list(server._jobs.values()):
            job.cancel()
        return refused

    assert "already running" in run(go())["error"]
    assert "no task" in server.task_status("tabcdef")["error"]


def test_a_task_running_at_a_restart_is_marked_failed(data):
    (data / "delegate.json").write_text(json.dumps({"t000001": {
        "id": "t000001", "goal": "g", "state": "running", "result": "", "started_at": 5.0, "finished_at": None}}))
    assert server.task_result("t000001")["state"] == "failed"


def test_the_model_is_a_fake_by_default_and_only_delegate_gets_the_key(monkeypatch):
    assert isinstance(server.make_model(), FakeModel)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    assert "ANTHROPIC_API_KEY" in _server_env(None, TASK_SERVERS["delegate"])
    assert "ANTHROPIC_API_KEY" not in _server_env(None, TASK_SERVERS["workspace"])


def test_the_worker_sees_the_workspace_schemas():
    specs = {s["name"]: s for s in run(worker.tool_specs())}
    assert set(specs) == {"bash", *worker.FILE_TOOLS, *worker.YES_TOOLS}
    assert specs["bash"]["input_schema"]["required"] == ["command"]


# The announcement


def _watch(tmp_path, tasks, quiet=lambda: True, start=100.0):
    (tmp_path / "delegate.json").write_text(json.dumps({t["id"]: t for t in tasks}))
    spoken, events = [], []

    async def speak(text):
        spoken.append(text)

    watch = DelegateWatch(tmp_path / "delegate.json", speak, quiet, lambda e, **f: events.append(e), start=start)
    return watch, spoken, events


def _task(id_, state, finished_at=110.0, result="I wrote the page."):
    return {"id": id_, "goal": "g", "state": state, "result": result, "started_at": 101.0, "finished_at": finished_at}


def test_a_finished_task_is_announced_once_at_a_quiet_moment(tmp_path):
    floor = {"quiet": False}
    watch, spoken, events = _watch(tmp_path, [_task("a", "done")], quiet=lambda: floor["quiet"])
    run(watch.check())
    assert spoken == []
    floor["quiet"] = True
    run(watch.check())
    run(watch.check())
    assert spoken == ["Your background task is done. I wrote the page."] and events == ["task_finished"]


def test_one_announcement_per_tick(tmp_path):
    watch, spoken, _ = _watch(tmp_path, [_task("a", "done"), _task("b", "needs_yes", 111.0, "It needs your yes for: x.")])
    run(watch.check())
    assert len(spoken) == 1
    run(watch.check())
    assert spoken[1] == "Your background task stopped. It needs your yes for: x."


def test_running_cancelled_and_old_tasks_stay_quiet(tmp_path):
    tasks = [_task("a", "running", None), _task("b", "cancelled"), _task("c", "done", 90.0)]
    watch, spoken, _ = _watch(tmp_path, tasks)
    run(watch.check())
    assert spoken == []


def test_no_file_yet_is_fine(tmp_path):
    async def speak(_text):
        raise AssertionError

    run(DelegateWatch(tmp_path / "delegate.json", speak).check())


def test_a_long_result_is_cut_to_what_can_be_heard():
    phrase = finished_phrase(_task("a", "done", result="word " * 200))
    assert len(phrase) < 330 and phrase.endswith("Ask me for the full result.")
