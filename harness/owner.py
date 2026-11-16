"""Who the agent belongs to (ADR-050): one small public file, read by the prompt, STT and TTS."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from harness.config import CONFIGS_DIR


@dataclass(frozen=True)
class Owner:
    name: str
    first_name: str
    about: str = ""
    said_as: str = ""
    misheard: tuple[str, ...] = ()
    search_hints: tuple[str, ...] = ()
    keyterms: tuple[str, ...] = ()
    pronounce: dict[str, str] = field(default_factory=dict)


def load_owner(path: Path = CONFIGS_DIR / "owner.yaml") -> Owner:
    raw = yaml.safe_load(path.read_text()) or {}
    name = str(raw["name"]).strip()
    return Owner(
        name=name,
        first_name=str(raw.get("first_name") or name.split()[0]),
        about=str(raw.get("about") or ""),
        said_as=str(raw.get("said_as") or ""),
        misheard=tuple(raw.get("misheard") or ()),
        search_hints=tuple(raw.get("search_hints") or ()),
        keyterms=tuple(raw.get("keyterms") or ()),
        pronounce=dict(raw.get("pronounce") or {}),
    )


def either(items: tuple[str, ...]) -> str:
    """A spoken list: 'a', 'a or b', 'a, b or c'."""
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} or {items[-1]}"
