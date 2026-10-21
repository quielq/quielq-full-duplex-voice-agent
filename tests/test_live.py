"""Live provider tests. They spend real (small) money, so they are skipped unless ME3_LIVE=1.

    ME3_LIVE=1 uv run pytest -q tests/test_live.py

Each run costs about $0.0007 at list price (Flux, Groq gpt-oss-20b, Aura-2) and is
metered into the real cost ledger, so the provider caps in configs/providers.yaml apply.
"""

import json
import os
import shutil
import subprocess

import pytest

from harness.bot import run_bot
from harness.config import load_config

pytestmark = pytest.mark.skipif(os.environ.get("ME3_LIVE") != "1", reason="set ME3_LIVE=1 to spend")


@pytest.fixture(scope="module")
def spoken_request(tmp_path_factory):
    if not shutil.which("say"):
        pytest.skip("macOS `say` not available")
    path = tmp_path_factory.mktemp("audio") / "request.wav"
    subprocess.run(
        ["say", "-o", str(path), "--data-format=LEI16@16000", "Set a timer for one minute."],
        check=True,
    )
    return path


@pytest.mark.parametrize("transport", ["stdio", "http"])
async def test_live_gate1(tmp_path, spoken_request, transport):
    cfg = load_config("mac")  # real ledger path, so live spend counts against the caps
    cfg.providers["stages"] = {"stt": "deepgram_flux", "llm": "groq", "tts": "deepgram_aura2"}
    data = tmp_path / "data"
    r = await run_bot(cfg, input_mode="wav", wav=spoken_request, mcp_transport=transport,
                      data_dir=str(data), handle_sigint=False, reply_timeout_s=30)  # fmt: skip
    assert "timer" in " ".join(r.transcripts).lower()
    assert [c["name"] for c in r.tool_calls] == ["set_timer"]
    assert r.tool_calls[0]["args"]["duration_seconds"] == 60
    assert r.spoken_text
    assert len(json.loads((data / "clock.json").read_text())) == 1
    assert 0 < r.session_usd < 0.01
