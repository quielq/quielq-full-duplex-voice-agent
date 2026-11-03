"""Pi port pieces that can be checked off the Pi: stats parsing, clips, and safe service defaults."""

import asyncio
import subprocess
import wave
from pathlib import Path

import pytest

from bench import doctor
from bench.echo_probe import OUT_RATE, read_clip
from harness import system_stats
from harness.config import REPO_ROOT, load_config


def test_temperature_and_memory_parse(tmp_path):
    temp = tmp_path / "temp"
    temp.write_text("48750\n")
    mem = tmp_path / "meminfo"
    mem.write_text("MemTotal:  8192000 kB\nMemFree: 1 kB\nMemAvailable:  4096000 kB\n")
    assert system_stats.read_temp_c(temp) == 48.8
    assert system_stats.read_mem_mb(mem) == (8000, 4000)


def test_missing_readings_are_none_not_errors(tmp_path):
    assert system_stats.read_temp_c(tmp_path / "none") is None
    assert system_stats.read_mem_mb(tmp_path / "none") is None


def test_throttle_flags_parse(monkeypatch):
    monkeypatch.setattr(system_stats.shutil, "which", lambda name: "/usr/bin/vcgencmd")
    done = subprocess.CompletedProcess([], 0, stdout="throttled=0x50005\n")
    monkeypatch.setattr(system_stats.subprocess, "run", lambda *a, **k: done)
    assert system_stats.read_throttled() == 0x50005


def _pi_rows(monkeypatch, **readings):
    monkeypatch.setattr(doctor.sys, "platform", "linux")
    monkeypatch.setattr(doctor, "read_temp_c", lambda: readings.get("temp"))
    monkeypatch.setattr(doctor, "read_mem_mb", lambda: readings.get("mem"))
    monkeypatch.setattr(doctor, "read_throttled", lambda: readings.get("throttled"))
    return {name: status for status, name, *_ in doctor.check_pi(load_config("pi"), "aarch64")}


def test_a_healthy_pi_passes(monkeypatch):
    rows = _pi_rows(monkeypatch, temp=50.0, mem=(8000, 4000), throttled=0)
    assert set(rows.values()) == {"ok"} and len(rows) == 4


def test_a_hot_throttled_pi_warns(monkeypatch):
    rows = _pi_rows(monkeypatch, temp=80.0, mem=(4000, 100), throttled=0x50005)
    assert rows["temperature"] == rows["memory"] == rows["throttling"] == "warn"


def test_read_clip_refuses_the_wrong_rate(tmp_path):
    path = tmp_path / "x.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(bytes(100))
    with pytest.raises(SystemExit):
        read_clip(path)


def test_read_clip_returns_samples(tmp_path):
    path = tmp_path / "x.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(OUT_RATE)
        w.writeframes(bytes(100))
    assert read_clip(path) == bytes(100)


@pytest.mark.parametrize("script", ["scripts/pi_setup.sh", "scripts/pi_kiosk.sh"])
def test_shell_scripts_parse(script):
    subprocess.run(["bash", "-n", str(REPO_ROOT / script)], check=True)


def test_the_service_cannot_spend_until_someone_edits_it():
    """ADR-030: a reboot must not start paid calls."""
    unit = (REPO_ROOT / "scripts/systemd/me3-bot.service").read_text()
    assert "--stages" not in unit and "--profile pi" in unit
    active = [l for l in Path(REPO_ROOT / "scripts/systemd/bot.env.example").read_text().splitlines()
              if l and not l.startswith("#")]  # fmt: skip
    assert active == ["ME3_BOT_ARGS="]


def test_face_stays_on_this_machine():
    assert load_config("pi").profile["ui"]["host"] == "127.0.0.1"


async def test_stats_loop_emits_and_can_be_cancelled():
    events = []
    task = asyncio.create_task(system_stats.SystemStats(lambda name, **f: events.append((name, f)), 0.01).run())
    async with asyncio.timeout(5):
        while not events:
            await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events[0][0] == "system_stats" and "load_1m" in events[0][1]


def test_a_late_loop_is_logged_and_a_normal_one_is_not():
    seen = []
    lag = system_stats.LoopLag(lambda name, **f: seen.append((name, f)), tick_s=0.05)
    lag.check(0.06)
    assert seen == []
    lag.check(1.05)
    assert seen == [("loop_lag", {"ms": 1000})]
