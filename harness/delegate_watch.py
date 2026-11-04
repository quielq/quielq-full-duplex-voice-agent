"""Say when a background task has finished, at the next quiet moment (ADR-035).

The delegate server keeps its tasks in delegate.json. This reads that file once a second, the
way timer_watch.py reads clock.json, and speaks only when nobody is talking and no reply is on
its way, so a result never cuts into a turn. A task that finished before this session started,
or that the user cancelled, stays quiet.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

ANNOUNCED = ("done", "needs_yes", "failed", "stopped")
MAX_SPOKEN_CHARS = 240   # the same limit as a read-back: longer cannot be heard in full


def finished_phrase(task: dict) -> str:
    result = " ".join(str(task.get("result", "")).split())
    if len(result) > MAX_SPOKEN_CHARS:
        result = result[:MAX_SPOKEN_CHARS].rsplit(" ", 1)[0] + ", and more. Ask me for the full result."
    if task["state"] == "done":
        return f"Your background task is done. {result}".strip()
    return f"Your background task stopped. {result}".strip()


class DelegateWatch:
    def __init__(
        self,
        path: Path,
        speak: Callable[[str], Awaitable[None]],
        quiet: Callable[[], bool] = lambda: True,
        on_event: Callable[..., object] | None = None,
        start: float | None = None,
    ):
        self._path = path
        self._speak = speak
        self._quiet = quiet
        self._on_event = on_event or (lambda *a, **k: None)
        self._start = time.time() if start is None else start
        self._announced: set[str] = set()

    def _finished(self) -> list[dict]:
        try:
            tasks = json.loads(self._path.read_text())
        except (OSError, ValueError):  # no tasks yet
            return []
        done = [t for t in tasks.values()
                if t["state"] in ANNOUNCED and (t.get("finished_at") or 0) > self._start
                and t["id"] not in self._announced]  # fmt: skip
        return sorted(done, key=lambda t: t["finished_at"])

    async def check(self) -> None:
        for task in self._finished():
            if not self._quiet():
                return  # try again on the next tick
            self._announced.add(task["id"])
            phrase = finished_phrase(task)
            self._on_event("task_finished", task_id=task["id"], state=task["state"], phrase=phrase)
            await self._speak(phrase)
            return  # one at a time: the next waits until this one has been said

    async def run(self, every_s: float = 1.0) -> None:
        while True:
            await self.check()
            await asyncio.sleep(every_s)
