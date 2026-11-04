"""Synthesise the stored phrases into assets/fallback_wavs/ through our voice-speak server.

A paid Cartesia call, metered in the cost ledger: the waiting phrases are about 60 characters,
about $0.003 at list price, and the two recovery clips about 100, about $0.005. It refuses unless ME3_LIVE=1. The clips are synthetic, in the live
voice from configs/providers.yaml, and must be made again when that voice changes.

    ME3_LIVE=1 uv run python -m bench.make_phrases                 # the waiting phrases
    ME3_LIVE=1 uv run python -m bench.make_phrases --recovery      # network_lost and cap_reached (ADR-037)
    ME3_LIVE=1 uv run python -m bench.make_phrases --all           # every phrase
    ME3_LIVE=1 uv run python -m bench.make_phrases one_sec on_it   # just these
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import uuid
import wave

from bench.cost_ledger import CostLedger
from harness.config import load_config
from harness.phrase_cache import PHRASE_DIR, PHRASES
from harness.voice_relay import RelayClient

MODEL = "sonic-3.6"  # Pipecat 1.13.0's default for CartesiaTTSService
VERSION = "2026-03-01"  # the Cartesia API version Pipecat 1.13.0 is written against


def recovery_phrases() -> list[str]:
    """The clips the harness plays when it cannot use TTS: the network is gone or the cap is hit."""
    r = load_config("mac").profile["recovery"]
    return [r["network_clip"], r["cap_clip"]]


async def synthesise(client: RelayClient, text: str, voice_id: str, rate: int) -> bytes:
    ws = await client.open("speak", {"query": "", "version": VERSION})
    ctx = str(uuid.uuid4())
    await ws.send(json.dumps({
        "transcript": text, "continue": False, "context_id": ctx, "model_id": MODEL,
        "voice": {"mode": "id", "id": voice_id},
        "output_format": {"container": "raw", "encoding": "pcm_s16le", "sample_rate": rate},
        "add_timestamps": False,
    }))  # fmt: skip
    pcm = bytearray()
    try:
        async with asyncio.timeout(20):
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("type") == "chunk":
                    pcm += base64.b64decode(msg["data"])
                elif msg.get("type") == "done":
                    break
                elif msg.get("type") == "error":
                    raise RuntimeError(f"cartesia: {msg.get('error') or msg.get('title') or msg}")
    finally:
        await ws.close()
    return bytes(pcm)


async def main(names: list[str]) -> None:
    cfg = load_config("mac")
    p = cfg.providers["providers"]["cartesia"]
    rate = cfg.profile["audio"]["out_sample_rate"]
    data_dir = cfg.path("data_dir")
    ledger = CostLedger.from_providers_config(cfg.providers, data_dir / "cost_ledger.jsonl")
    price = cfg.providers["prices"]["cartesia_per_char"]
    client = RelayClient("speak", "stdio", str(data_dir), lambda *a, **k: None)
    PHRASE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        for name in names:
            text = PHRASES[name]
            ledger.authorize("cartesia")
            pcm = await synthesise(client, text, p["voice_id"], rate)
            ledger.record("cartesia", "tts_chars", len(text), len(text) * price, run_id="make_phrases")
            path = PHRASE_DIR / f"{name}.wav"
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(rate)
                w.writeframes(pcm)
            print(f"{name}: {len(pcm) / 2 / rate:.2f} s  {text!r}  -> {path.relative_to(PHRASE_DIR.parent.parent)}")
    finally:
        await client.close()
    print(f"cartesia today: ${ledger.spent_today('cartesia'):.4f} of ${ledger.cap('cartesia'):.2f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("names", nargs="*", help="phrase names; default: the waiting phrases in configs/mac.yaml")
    ap.add_argument("--all", action="store_true", help="every phrase in harness/phrase_cache.py")
    ap.add_argument("--recovery", action="store_true", help="the network-loss and spending-cap clips")
    args = ap.parse_args()
    if os.environ.get("ME3_LIVE") != "1":
        raise SystemExit("this run spends money (Cartesia): set ME3_LIVE=1")
    chosen = recovery_phrases() if args.recovery else (
        list(PHRASES) if args.all else args.names or load_config("mac").profile["filler"]["phrases"])
    unknown = [n for n in chosen if n not in PHRASES]
    if unknown:
        raise SystemExit(f"unknown phrases: {', '.join(unknown)}")
    asyncio.run(main(chosen))
