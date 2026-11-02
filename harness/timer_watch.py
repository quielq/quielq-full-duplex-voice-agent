"""Say when a timer or alarm is due.

The clock server only stores times in clock.json, so nothing else would ever speak up. This
reads the same file once a second, from the bot. A time that passed before this session started
stays quiet: clock.json keeps old entries, and they are not news. The real voice servers would
take this over if they are built.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from pathlib import Path


def due_phrase(item: dict) -> str:
    label = item.get("label", "").strip()
    if item.get("kind") == "alarm":
        return f"Your {label} alarm is going off." if label else "Your alarm is going off."
    return f"Your {label} timer is done." if label else "Your timer is done."


class TimerWatch:
    def __init__(
        self,
        path: Path,
        speak: Callable[[str], Awaitable[None]],
        on_event: Callable[..., object] | None = None,
        start: float | None = None,
    ):
        self._path = path
        self._speak = speak
        self._on_event = on_event or (lambda *a, **k: None)
        self._start = time.time() if start is None else start
        self._announced: set[str] = set()

    def _due(self, now: float) -> list[dict]:
        try:
            items = json.loads(self._path.read_text())
        except (OSError, ValueError):  # no timers yet, or the server is mid write
            return []
        due = [i for i in items.values()
               if self._start < i["fires_at"] <= now and i["id"] not in self._announced]  # fmt: skip
        return sorted(due, key=lambda i: i["fires_at"])

    async def check(self, now: float | None = None) -> None:
        for item in self._due(time.time() if now is None else now):
            self._announced.add(item["id"])
            phrase = due_phrase(item)
            self._on_event("timer_due", kind=item["kind"], label=item["label"], phrase=phrase)
            await self._speak(phrase)

    async def run(self, every_s: float = 1.0) -> None:
        while True:
            await self.check()
            await asyncio.sleep(every_s)
