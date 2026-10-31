"""Pre-synthesised phrases in assets/fallback_wavs/, played with no TTS call.

The waiting phrase uses this now. The network-loss phrases and the spending-cap notice share
it when they are built. Clips are synthetic, never recordings of people, and must be
regenerated when the voice changes. Making them is a paid TTS call, so until that is approved
a missing clip falls back to a short quiet tone and the run logs that it did.
"""

from __future__ import annotations

import math
import struct
from pathlib import Path

from harness.config import REPO_ROOT
from harness.file_input import read_pcm

PHRASE_DIR = REPO_ROOT / "assets" / "fallback_wavs"
PHRASES = {
    "one_sec": "One sec.",
    "network_lost": "I've lost my connection. Give me a moment.",
    "cap_reached": "I've reached today's spending limit, so I have to stop here.",
}


def placeholder_tone(sample_rate: int, ms: int = 300) -> bytes:
    n = sample_rate * ms // 1000
    return struct.pack(f"<{n}h", *(int(800 * math.sin(2 * math.pi * 330 * i / sample_rate)) for i in range(n)))


def load_phrase(name: str, sample_rate: int, phrase_dir: Path = PHRASE_DIR) -> tuple[bytes, bool]:
    """Return (mono 16-bit PCM, whether it is the real clip)."""
    if name not in PHRASES:
        raise KeyError(f"unknown phrase {name!r}")
    path = phrase_dir / f"{name}.wav"
    if not path.exists():
        return placeholder_tone(sample_rate), False
    pcm, rate = read_pcm(path)
    if rate != sample_rate:
        raise ValueError(f"{path.name}: {rate} Hz, the output runs at {sample_rate} Hz")
    return pcm, True
