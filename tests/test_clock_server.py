import json

import pytest
from fastmcp import Client

from servers.tasks.clock.server import mcp


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ME3_DATA_DIR", str(tmp_path))


async def test_every_tool_is_annotated():
    async with Client(mcp) as c:
        for tool in await c.list_tools():
            hints = tool.annotations.model_dump(by_alias=True, exclude_none=True)
            for key in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"):
                assert key in hints, f"{tool.name} missing {key}"


async def test_timer_roundtrip_and_persistence(tmp_path):
    async with Client(mcp) as c:
        t = (await c.call_tool("set_timer", {"duration_seconds": 60})).structured_content
        assert t["remaining_seconds"] == 60 and t["status"] == "pending"
        listed = (await c.call_tool("list_timers", {})).structured_content["result"]
        assert [x["id"] for x in listed] == [t["id"]]
    # State is on disk, so a restart keeps it.
    assert t["id"] in json.loads((tmp_path / "clock.json").read_text())
    async with Client(mcp) as c:
        r = (await c.call_tool("cancel_timer", {"timer_id": t["id"]})).structured_content
        assert r["cancelled"] is True
        assert (await c.call_tool("list_timers", {})).structured_content["result"] == []


async def test_alarm_rejects_bad_time():
    async with Client(mcp) as c:
        with pytest.raises(Exception):
            await c.call_tool("set_alarm", {"time_hhmm": "25:00"})


async def test_tool_calls_are_logged(tmp_path):
    async with Client(mcp) as c:
        await c.call_tool("set_timer", {"duration_seconds": 5})
    lines = (tmp_path / "tool_calls.jsonl").read_text().splitlines()
    rec = json.loads(lines[-1])
    assert rec["tool"] == "set_timer" and rec["ok"] and rec["duration_ms"] >= 0
