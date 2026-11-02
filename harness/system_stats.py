"""CPU load, temperature, memory, and throttling, logged during live runs (step 7).

Reads what the machine offers: on the Mac that is load alone, on the Pi all of it.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

INTERVAL_S = 5.0
THROTTLE_TIMEOUT_S = 2.0


def read_temp_c(path: Path = Path("/sys/class/thermal/thermal_zone0/temp")) -> float | None:
    try:
        return round(int(path.read_text()) / 1000, 1)
    except (OSError, ValueError):
        return None


def read_mem_mb(path: Path = Path("/proc/meminfo")) -> tuple[int, int] | None:
    """Total and available memory."""
    try:
        fields = dict(line.split(":", 1) for line in path.read_text().splitlines() if ":" in line)
        return int(fields["MemTotal"].split()[0]) // 1024, int(fields["MemAvailable"].split()[0]) // 1024
    except (OSError, KeyError, ValueError, IndexError):
        return None


def read_throttled() -> int | None:
    """The Pi firmware's flags: 0 is healthy, the low bits are now and the high bits are since boot."""
    tool = shutil.which("vcgencmd")
    if not tool:
        return None
    try:
        done = subprocess.run([tool, "get_throttled"], capture_output=True, text=True, timeout=THROTTLE_TIMEOUT_S)
        return int(done.stdout.strip().split("=")[1], 16)
    except (OSError, subprocess.SubprocessError, IndexError, ValueError):
        return None


def sample() -> dict:
    stats: dict = {"load_1m": round(os.getloadavg()[0], 2)}
    if (temp := read_temp_c()) is not None:
        stats["temp_c"] = temp
    if mem := read_mem_mb():
        stats["mem_total_mb"], stats["mem_available_mb"] = mem
    if (throttled := read_throttled()) is not None:
        stats["throttled"] = hex(throttled)
    return stats


class SystemStats:
    def __init__(self, emit: Callable[..., object], interval_s: float = INTERVAL_S):
        self._emit = emit
        self._interval_s = interval_s

    async def run(self) -> None:
        while True:
            self._emit("system_stats", **await asyncio.to_thread(sample))
            await asyncio.sleep(self._interval_s)
