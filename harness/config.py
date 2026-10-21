"""Load a run profile (mac or pi) plus the provider selection, from configs/."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIGS_DIR = REPO_ROOT / "configs"
PROFILES = ("mac", "pi")


@dataclass
class Config:
    profile: dict[str, Any]
    providers: dict[str, Any]

    @property
    def name(self) -> str:
        return self.profile["profile"]

    def path(self, key: str) -> Path:
        """Resolve a path from the profile relative to the repo root (no absolute paths in configs)."""
        p = REPO_ROOT / self.profile["paths"][key]
        p.mkdir(parents=True, exist_ok=True)
        return p


def load_config(profile: str, configs_dir: Path = CONFIGS_DIR) -> Config:
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}, expected one of {PROFILES}")
    load_dotenv(REPO_ROOT / ".env")
    with open(configs_dir / f"{profile}.yaml") as f:
        prof = yaml.safe_load(f)
    with open(configs_dir / "providers.yaml") as f:
        providers = yaml.safe_load(f)
    return Config(profile=prof, providers=providers)
