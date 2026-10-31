"""Doctor: is this machine and repo fit to run ME3, and if not, what to do about it.

    uv run python -m bench.doctor                  # everything, about 30 s
    uv run python -m bench.doctor --quick          # skip MCP, model, and scripted-turn checks
    uv run python -m bench.doctor --offline        # skip provider reachability
    uv run python -m bench.doctor --only env,mcp   # sections by name
    uv run python -m bench.doctor --profile pi --json

Exit code 1 if any check fails, so it can gate a demo or a Pi deploy. Warnings do not fail.

Spends nothing. Reachability is a TLS handshake that sends no key and no request. Key values
are never read into a result, only whether each one is set. The scripted turn uses fakes,
and its data and logs go to a temporary folder.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import importlib.metadata
import io
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import tokenize
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

from bench.cost_ledger import CostLedger
from bench.run_report import all_logs, build_report
from harness.config import PROFILES, REPO_ROOT, Config, load_config
from harness.mcp_tools import TASK_SERVERS
from harness.providers import STAGE_PROVIDER

STATUS_MARK = {"ok": "ok  ", "warn": "WARN", "fail": "FAIL", "skip": "skip"}
SLOW_TLS_MS = 1000
HANDSHAKES = 5
SPEND_WARN_FRACTION = 0.8

PROVIDER_HOSTS = {
    "deepgram": "api.deepgram.com",
    "groq": "api.groq.com",
    "cartesia": "api.cartesia.ai",
    "anthropic": "api.anthropic.com",
    "openrouter": "openrouter.ai",
}

# Hard rules from CLAUDE.md, matched against code with spaces around punctuation removed.
BANNED_CODE = {
    r"\ballow_interruptions=": "removed Pipecat argument, silently ignored",
    r"\binterruption_strategies=": "removed Pipecat argument, silently ignored",
    r"\bMinWordsInterruptionStrategy\(": "removed Pipecat class",
    r"TransportParams\([^)]*vad_analyzer": "stale argument: use a VADProcessor",
    r"\bten[_-]?vad\b": "TEN VAD licence bars this project",
    r"TextAggregationMode\.TOKEN": "token aggregation commits unspoken text on interruption",
    r"\.register_tools\(": "deprecated: use client.tools() (ADR-003)",
}
KEY_SHAPES = re.compile(r"\b(gsk_[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9_-]{20,}|sk-or-[A-Za-z0-9_-]{20,})\b")
PROFILE_KEYS_THAT_MAY_DIFFER = {"profile"}


@dataclass
class Result:
    section: str
    name: str
    status: str  # ok | warn | fail | skip
    detail: str = ""
    fix: str = ""


def _ok(name: str, detail: str = "") -> tuple:
    return "ok", name, detail, ""


def _warn(name: str, detail: str, fix: str = "") -> tuple:
    return "warn", name, detail, fix


def _fail(name: str, detail: str, fix: str = "") -> tuple:
    return "fail", name, detail, fix


def _skip(name: str, detail: str) -> tuple:
    return "skip", name, detail, ""


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=20
    ).stdout.strip()


def check_python(cfg: Config) -> Iterator[tuple]:
    v = sys.version_info
    if (v.major, v.minor) == (3, 13):
        yield _ok("python", sys.version.split()[0])
    else:
        yield _fail("python", f"{v.major}.{v.minor}, need 3.13 (ADR-002)", "uv sync")
    wanted = {
        "pipecat-ai": lambda s: s == "1.13.0",
        "fastmcp": lambda s: s.split(".")[:2] >= ["4", "1"] and s.startswith("4."),
        "mcp": lambda s: s.startswith("2."),
    }
    for package, good in wanted.items():
        try:
            version = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            yield _fail(package, "not installed", "uv sync")
            continue
        yield _ok(package, version) if good(version) else _fail(package, f"{version} is off the pin", "uv sync")
    for module in ("pyaudio", "onnxruntime", "soxr", "livekit.rtc"):
        try:
            importlib.import_module(module)
            yield _ok(f"import {module}")
        except ImportError as e:
            yield _fail(f"import {module}", str(e), "uv sync")
    if not shutil.which("uv"):
        yield _warn("uv", "not on PATH", "install uv (ADR-002)")


def check_env(cfg: Config) -> Iterator[tuple]:
    if not (REPO_ROOT / ".env").exists():
        yield _warn(".env", "missing, so only fakes can run", "cp .env.example .env")
    in_use = _stage_providers(cfg)
    for name, p in cfg.providers["providers"].items():
        is_set = bool(os.environ.get(p["env_key"], "").strip())
        state = "set" if is_set else "not set"
        if name in in_use and not is_set:
            yield _fail(p["env_key"], f"{state}, but a stage selects {name}", "add it to .env")
        else:
            yield _ok(p["env_key"], state)


def _stage_providers(cfg: Config) -> set[str]:
    stages = cfg.providers["stages"].values()
    return {STAGE_PROVIDER[s] for s in stages if s in STAGE_PROVIDER}


def check_config(cfg: Config) -> Iterator[tuple]:
    stages = cfg.providers["stages"]
    if all(str(v).startswith("fake") for v in stages.values()):
        yield _ok("stages default to fakes", str(stages))
    else:
        yield _warn("stages", f"real provider by default, a plain run spends: {stages}",
                    "keep fakes in providers.yaml and pass --stages per run")  # fmt: skip
    for stage, choice in stages.items():
        if choice.startswith("fake"):
            continue
        provider = STAGE_PROVIDER.get(choice)
        if provider is None:
            yield _fail(f"stage {stage}", f"unknown choice {choice!r}")
        elif float(cfg.providers["providers"][provider].get("daily_cap_usd", 0)) <= 0:
            yield _fail(f"stage {stage}", f"{choice}: {provider} cap is zero, every call is refused")

    for key in ("in_sample_rate", "out_sample_rate"):
        if cfg.profile["audio"][key] not in (8000, 16000, 24000, 44100, 48000):
            yield _warn(f"audio.{key}", str(cfg.profile["audio"][key]))
    start = cfg.profile["vad"]["start_secs"]
    if not 0.064 <= start <= 0.1:
        yield _warn("vad.start_secs", f"{start}: ducking within 100 ms needs 0.064 to 0.1")
    words = [*cfg.profile["barge_in"]["allowlist"], *cfg.tool_policy["undo_words"], *cfg.tool_policy["no_words"]]
    if bad := [w for w in words if not isinstance(w, str)]:
        yield _fail("word lists", f"not strings: {bad}", "quote them: a bare no is a YAML boolean")
    else:
        yield _ok("word lists", "all strings")

    others = [p for p in PROFILES if p != cfg.name]
    for other in others:
        drift = _shape_drift(cfg.profile, load_config(other).profile)
        if drift:
            yield _warn(f"{cfg.name} vs {other}", f"keys differ: {sorted(drift)}", "the profiles must share a shape")
        else:
            yield _ok(f"{cfg.name} vs {other}", "same keys")


def _shape_drift(a: dict, b: dict, prefix: str = "") -> set[str]:
    out = set()
    for key in a.keys() | b.keys():
        path = f"{prefix}{key}"
        if key not in a or key not in b:
            out.add(path)
        elif isinstance(a[key], dict) and isinstance(b[key], dict):
            out |= _shape_drift(a[key], b[key], f"{path}.")
    return out


def banned_uses(source: str) -> list[str]:
    """Why ``source`` breaks a hard rule. Code only: docs may warn against these by name."""
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        code = " ".join(t.string for t in tokens if t.type not in (tokenize.STRING, tokenize.COMMENT))
    except (tokenize.TokenError, IndentationError):
        code = source
    code = re.sub(r"\s*([().=])\s*", r"\1", code)
    return [why for pattern, why in BANNED_CODE.items() if re.search(pattern, code, re.I)]


def check_rules(cfg: Config) -> Iterator[tuple]:
    """Hard rules that a grep can catch. Tracked files only, so .env and logs stay unread."""
    tracked = _git("ls-files").splitlines()
    if not tracked:
        yield _skip("repo rules", "not a git checkout")
        return
    if ".env" in tracked:
        yield _fail(".env tracked", "secrets are in git", "git rm --cached .env, then rotate the keys")
    else:
        yield _ok(".env not tracked")
    audio = [f for f in tracked if f.endswith((".wav", ".mp3", ".flac")) and not f.startswith("assets/fallback_wavs/")]
    if audio:
        yield _fail("audio in git", f"{audio[:3]}", "never commit recordings of people")
    else:
        yield _ok("no recordings tracked")

    hits, keys, dashes = [], [], []
    this_file = str(Path(__file__).relative_to(REPO_ROOT))
    for rel in tracked:
        path = REPO_ROOT / rel
        if rel == "uv.lock" or not path.is_file() or path.suffix in (".wav", ".png", ".jpg", ".pdf"):
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        if KEY_SHAPES.search(text):
            keys.append(rel)  # the file, never the match
        if rel.endswith((".md", ".py", ".yaml")) and re.search("[–—]", text):
            dashes.append(rel)
        if rel.endswith(".py") and rel != this_file and not rel.startswith("tests/"):
            hits += [f"{rel}: {why}" for why in banned_uses(text)]
    yield _fail("key-shaped strings", str(keys), "remove and rotate") if keys else _ok("no key-shaped strings")
    yield _warn("em or en dashes", str(dashes[:5]), "rule 5") if dashes else _ok("no em or en dashes")
    yield _fail("banned APIs", "; ".join(hits[:5])) if hits else _ok("no banned APIs")


def check_git(cfg: Config) -> Iterator[tuple]:
    branch = _git("branch", "--show-current")
    if not branch:
        yield _skip("git", "not a git checkout")
        return
    yield _ok("branch", branch)
    dirty = _git("status", "--short").splitlines()
    yield _warn("working tree", f"{len(dirty)} uncommitted paths") if dirty else _ok("working tree", "clean")
    ahead = _git("rev-list", "--count", "origin/main..main")
    if ahead and ahead != "0":
        yield _warn("main", f"{ahead} commits not pushed", "git push origin main, when ready")
    unmerged = _git("branch", "--no-merged", "main", "--format=%(refname:short)").split()
    yield _warn("unmerged branches", " ".join(unmerged)) if unmerged else _ok("branches", "all merged into main")


def check_visibility(cfg: Config) -> Iterator[tuple]:
    """Rule 8: the repo stays private until the patent decision."""
    if not shutil.which("gh"):
        yield _skip("repo visibility", "gh not installed")
        return
    try:
        out = subprocess.run(
            ["gh", "repo", "view", "--json", "visibility", "-q", ".visibility"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=10,
        ).stdout.strip()  # fmt: skip
    except subprocess.TimeoutExpired:
        yield _skip("repo visibility", "gh timed out")
        return
    if not out:
        yield _skip("repo visibility", "gh returned nothing, signed in?")
    elif out == "PRIVATE":
        yield _ok("repo visibility", "private")
    else:
        yield _fail("repo visibility", out, "make it private: nothing is published before the patent decision")


def check_spend(cfg: Config) -> Iterator[tuple]:
    path = cfg.path("data_dir") / "cost_ledger.jsonl"
    bad = 0
    if path.exists():
        with open(path) as f:
            for line in f:
                try:
                    json.loads(line)
                except json.JSONDecodeError:
                    bad += 1
    if bad:
        yield _fail("ledger", f"{bad} unreadable lines, so caps cannot be trusted", f"repair {path.name}")
        return
    ledger = CostLedger.from_providers_config(cfg.providers, path)
    for name in cfg.providers["providers"]:
        cap, spent = ledger.cap(name), ledger.spent_today(name)
        detail = f"${spent:.4f} of ${cap:.2f} today"
        if cap > 0 and spent >= cap:
            yield _fail(name, f"{detail}, cap reached, calls refused until tomorrow")
        elif cap > 0 and spent >= cap * SPEND_WARN_FRACTION:
            yield _warn(name, detail)
        elif spent > 0 and cap <= 0:
            yield _warn(name, f"{detail}: spent with a zero cap")
        else:
            yield _ok(name, detail)


def check_audio(cfg: Config) -> Iterator[tuple]:
    from harness.audio_io import is_builtin_speaker, list_devices, output_device_name, resolve_device

    audio = cfg.profile["audio"]
    try:
        devices = list_devices()
    except Exception as e:
        yield _fail("PortAudio", f"{type(e).__name__}: {e}", "on the Pi: portaudio19-dev")
        return
    inputs = [d["name"] for d in devices if d["maxInputChannels"] > 0]
    outputs = [d["name"] for d in devices if d["maxOutputChannels"] > 0]
    yield _ok("input devices", ", ".join(inputs)) if inputs else _fail("input devices", "none")
    yield _ok("output devices", ", ".join(outputs)) if outputs else _fail("output devices", "none")
    try:
        index = resolve_device(audio.get("output_device"), "output")
        resolve_device(audio.get("input_device"), "input")
        output = output_device_name(index)
    except (RuntimeError, OSError) as e:
        yield _fail("configured devices", str(e), "uv run python -m harness.audio_io")
        return
    if is_builtin_speaker(output):
        yield _warn("output", f"{output}: live runs are refused without --echo-cancel", "plug in headphones")
    else:
        yield _ok("output", output)


def check_reachability(cfg: Config) -> Iterator[tuple]:
    """A TLS handshake only: no key, no request, no spend. ADR-015's slow delivery does not
    show up here, because a handshake says nothing about sustained audio."""
    in_use = _stage_providers(cfg)
    for provider, host in PROVIDER_HOSTS.items():
        if provider not in cfg.providers["providers"]:
            continue
        try:
            times = sorted(tls_handshake_ms(host) for _ in range(HANDSHAKES))
        except OSError as e:
            make = _fail if provider in in_use else _warn
            yield make(host, f"{type(e).__name__}: {e}", "check the network or the provider status page")
            continue
        median, worst = times[len(times) // 2], times[-1]
        detail = f"handshake median {median:.0f} ms, worst {worst:.0f} ms"
        yield _warn(host, f"{detail}, slow") if median > SLOW_TLS_MS else _ok(host, detail)


def tls_handshake_ms(host: str) -> float:
    start = time.perf_counter()
    with socket.create_connection((host, 443), timeout=5) as raw:
        with ssl.create_default_context().wrap_socket(raw, server_hostname=host):
            pass
    return (time.perf_counter() - start) * 1000


def check_mcp(cfg: Config) -> Iterator[tuple]:
    with tempfile.TemporaryDirectory() as tmp:
        for row in asyncio.run(_mcp_rows(cfg, tmp)):
            yield row


async def _mcp_rows(cfg: Config, data_dir: str) -> list[tuple]:
    from harness.mcp_tools import server_client
    from harness.tool_policy import HINT_FIELDS, apply_override, default_rule, tool_hints

    rows: list[tuple] = []
    owners: dict[str, list[str]] = {}
    hints: dict[str, dict | None] = {}
    for name, module in TASK_SERVERS.items():
        start = time.perf_counter()
        try:
            async with server_client(module, "stdio", data_dir) as client, client:
                await client.tools()
                found = await tool_hints(client)
        except Exception as e:
            rows.append(_fail(f"server {name}", f"{type(e).__name__}: {e}", f"uv run python -m {module}"))
            continue
        for tool in found:
            owners.setdefault(tool, []).append(name)
        hints |= found
        rows.append(_ok(f"server {name}", f"{len(found)} tools, {(time.perf_counter() - start):.1f} s"))

    dupes = {t: s for t, s in owners.items() if len(s) > 1}
    rows.append(_fail("unique tool names", str(dupes)) if dupes else _ok("unique tool names", f"{len(owners)} tools"))
    missing = [t for t, h in hints.items() if not h or any(h.get(f) is None for f in HINT_FIELDS)]
    rows.append(_fail("hints", f"missing on {missing}") if missing else _ok("hints", "all four on every tool"))

    overrides = cfg.tool_policy.get("tools") or {}
    table: dict[str, list[str]] = {}
    for tool, hint in hints.items():
        rule = apply_override(default_rule(tool, hint, cfg.tool_policy["defaults"]), overrides.get(tool))
        table.setdefault(f"{rule.permission}{'+undo' if rule.undo_s else ''}", []).append(tool)
    for kind, tools in sorted(table.items()):
        rows.append(_ok(f"policy {kind}", ", ".join(sorted(tools))))
    return rows


def check_models(cfg: Config) -> Iterator[tuple]:
    """Load the local models and time one inference: this is what limits the Pi."""
    import numpy as np

    from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
    from pipecat.audio.vad.silero import SileroVADAnalyzer

    start = time.perf_counter()
    vad = SileroVADAnalyzer()
    vad.set_sample_rate(16000)
    frame = np.zeros(vad.num_frames_required(), dtype=np.int16).tobytes()
    loaded = time.perf_counter()
    for _ in range(50):
        vad.voice_confidence(frame)
    per_frame = (time.perf_counter() - loaded) / 50 * 1000
    budget = vad.num_frames_required() / 16
    yield _ok("Silero VAD", f"load {(loaded - start):.2f} s, {per_frame:.2f} ms per frame") if per_frame < budget / 2 \
        else _warn("Silero VAD", f"{per_frame:.1f} ms per frame of {budget:.0f} ms")  # fmt: skip

    start = time.perf_counter()
    LocalSmartTurnAnalyzerV3()
    yield _ok("Smart Turn v3", f"load {(time.perf_counter() - start):.2f} s")


def check_scripted_turn(cfg: Config) -> Iterator[tuple]:
    """One full turn on fakes through the real bot, in a temporary folder."""
    from harness.bot import run_bot

    with tempfile.TemporaryDirectory() as tmp:
        run_cfg = load_config(cfg.name)
        run_cfg.providers["stages"] = {"stt": "fake", "llm": "fake", "tts": "fake"}
        run_cfg.profile["paths"] = {"logs_dir": f"{tmp}/logs", "data_dir": f"{tmp}/data"}
        start = time.perf_counter()
        try:
            result = asyncio.run(
                run_bot(run_cfg, input_mode="text", data_dir=f"{tmp}/data", reply_timeout_s=20,
                        handle_sigint=False, servers={"clock": TASK_SERVERS["clock"]})  # fmt: skip
            )
        except Exception as e:
            yield _fail("scripted turn", f"{type(e).__name__}: {e}", "uv run python -m harness.bot --input text")
            return
        took = time.perf_counter() - start
        names = [c["name"] for c in result.tool_calls]
        if names == ["set_timer"] and result.spoken_text:
            yield _ok("scripted turn", f"{names}, {took:.1f} s, ${result.session_usd:.2f}")
        else:
            yield _fail("scripted turn", f"tools {names}, spoke {result.spoken_text[:60]!r}")


def check_last_runs(cfg: Config) -> Iterator[tuple]:
    logs = all_logs(cfg.path("logs_dir"))
    if not logs:
        yield _skip("runs", "no logs yet")
        return
    yield _ok("logs", f"{len(logs)} runs")
    size_mb = sum(p.stat().st_size for p in logs) / 1e6
    if size_mb > 500:
        yield _warn("log size", f"{size_mb:.0f} MB", "archive old logs")
    last_live = next((r for r in map(build_report, reversed(logs[-300:])) if r.live), None)
    if last_live is None:
        yield _skip("last live run", "none in the last 300 runs")
        return
    errors = [a for a in last_live.anomalies if a.severity == "error"]
    warns = [a for a in last_live.anomalies if a.severity == "warn"]
    detail = f"{last_live.run_id}: {', '.join(a.code for a in [*errors, *warns]) or 'clean'}"
    fix = f"uv run python -m bench.run_report {last_live.run_id}"
    yield (_warn("last live run", detail, fix) if errors or warns else _ok("last live run", detail))


CHECKS: dict[str, tuple[Callable[[Config], Iterator[tuple]], str]] = {
    "python": (check_python, "fast"),
    "env": (check_env, "fast"),
    "config": (check_config, "fast"),
    "rules": (check_rules, "fast"),
    "git": (check_git, "fast"),
    "spend": (check_spend, "fast"),
    "audio": (check_audio, "fast"),
    "runs": (check_last_runs, "fast"),
    "visibility": (check_visibility, "network"),
    "network": (check_reachability, "network"),
    "mcp": (check_mcp, "slow"),
    "models": (check_models, "slow"),
    "turn": (check_scripted_turn, "slow"),
}


def run_checks(cfg: Config, sections: list[str]) -> list[Result]:
    results = []
    for section in sections:
        try:
            for status, name, detail, fix in CHECKS[section][0](cfg):
                results.append(Result(section, name, status, detail, fix))
        except Exception as e:  # a broken check must not hide the rest
            results.append(Result(section, "check crashed", "fail", f"{type(e).__name__}: {e}"))
    return results


def print_results(results: list[Result]) -> None:
    section = None
    for r in results:
        if r.section != section:
            section = r.section
            print(f"\n{section}")
        print(f"  {STATUS_MARK[r.status]}  {r.name:<28} {r.detail}")
        if r.fix and r.status in ("warn", "fail"):
            print(f"        -> {r.fix}")
    n = {s: sum(r.status == s for r in results) for s in STATUS_MARK}
    print(f"\n{n['ok']} ok, {n['warn']} warnings, {n['fail']} failures, {n['skip']} skipped")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", choices=PROFILES, default="mac")
    ap.add_argument("--quick", action="store_true", help="skip MCP, model, and scripted-turn checks")
    ap.add_argument("--offline", action="store_true", help="skip anything that touches the network")
    ap.add_argument("--only", default="", help=f"comma list from: {','.join(CHECKS)}")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if unknown := set(filter(None, args.only.split(","))) - set(CHECKS):
        ap.error(f"unknown sections {sorted(unknown)}")
    sections = list(filter(None, args.only.split(","))) or [
        name for name, (_, kind) in CHECKS.items()
        if not (args.quick and kind == "slow") and not (args.offline and kind == "network")
    ]  # fmt: skip

    from loguru import logger

    logger.remove()  # pipeline logs would bury the report
    os.environ.setdefault("FASTMCP_LOG_LEVEL", "ERROR")
    results = run_checks(load_config(args.profile), sections)
    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        print_results(results)
    sys.exit(1 if any(r.status == "fail" for r in results) else 0)


if __name__ == "__main__":
    main()
