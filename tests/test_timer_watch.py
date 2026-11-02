"""Due timers are said aloud once, and old ones stay quiet."""

import asyncio
import json

from harness.timer_watch import TimerWatch, due_phrase


def _watch(tmp_path, items, start=100.0):
    (tmp_path / "clock.json").write_text(json.dumps({i["id"]: i for i in items}))
    spoken, events = [], []

    async def speak(text):
        spoken.append(text)

    watch = TimerWatch(tmp_path / "clock.json", speak, lambda e, **f: events.append(e), start=start)
    return watch, spoken, events


def _timer(id_, fires_at, label="", kind="timer"):
    return {"id": id_, "kind": kind, "label": label, "fires_at": fires_at}


def test_a_due_timer_is_said_once(tmp_path):
    watch, spoken, events = _watch(tmp_path, [_timer("a", 110, "tea")])
    asyncio.run(watch.check(now=105))
    assert spoken == []
    asyncio.run(watch.check(now=111))
    asyncio.run(watch.check(now=112))
    assert spoken == ["Your tea timer is done."] and events == ["timer_due"]


def test_a_time_from_before_this_session_stays_quiet(tmp_path):
    watch, spoken, _ = _watch(tmp_path, [_timer("a", 90)])
    asyncio.run(watch.check(now=200))
    assert spoken == []


def test_no_clock_file_yet_is_fine(tmp_path):
    async def speak(_text):
        raise AssertionError

    asyncio.run(TimerWatch(tmp_path / "clock.json", speak).check())


def test_phrases():
    assert due_phrase(_timer("a", 1)) == "Your timer is done."
    assert due_phrase(_timer("a", 1, "wake up", "alarm")) == "Your wake up alarm is going off."
