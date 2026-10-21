"""Per-provider spend metering with a hard daily cap.

Every paid provider call goes through ``CostLedger``:

* ``authorize(provider)`` before a call or session. It raises ``SpendRefused``
  when the provider's cap is zero or today's spend has reached the cap.
* ``record(...)`` after usage is known, with the list-price cost in USD.

Entries are appended to a JSON-lines file so spend survives restarts and the
benchmark can read cost per run. Caps come from ``configs/providers.yaml``.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path


class SpendRefused(RuntimeError):
    """A provider call was refused because its cap is zero or already reached."""


@dataclass
class LedgerEntry:
    ts: float
    day: str
    provider: str
    kind: str  # e.g. "llm_input_tokens", "tts_chars", "stt_minutes"
    units: float
    usd: float
    run_id: str | None = None


class CostLedger:
    def __init__(self, path: Path, caps_usd: dict[str, float], *, today=date.today):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._caps = {k: float(v) for k, v in caps_usd.items()}
        self._today = today
        self._lock = threading.Lock()

    @classmethod
    def from_providers_config(cls, providers_cfg: dict, path: Path) -> "CostLedger":
        caps = {
            name: p.get("daily_cap_usd", 0.0) for name, p in providers_cfg["providers"].items()
        }
        return cls(path, caps)

    def cap(self, provider: str) -> float:
        # Unknown providers have no cap entry, which is the same as a zero cap.
        return self._caps.get(provider, 0.0)

    def spent_today(self, provider: str) -> float:
        today = self._today().isoformat()
        total = 0.0
        if not self._path.exists():
            return total
        with open(self._path) as f:
            for line in f:
                e = json.loads(line)
                if e["provider"] == provider and e["day"] == today:
                    total += e["usd"]
        return total

    def remaining(self, provider: str) -> float:
        return max(0.0, self.cap(provider) - self.spent_today(provider))

    def authorize(self, provider: str) -> None:
        cap = self.cap(provider)
        if cap <= 0:
            raise SpendRefused(f"{provider}: daily cap is zero, call refused")
        spent = self.spent_today(provider)
        if spent >= cap:
            raise SpendRefused(f"{provider}: daily cap ${cap:.2f} reached (spent ${spent:.4f})")

    def record(
        self, provider: str, kind: str, units: float, usd: float, run_id: str | None = None
    ) -> LedgerEntry:
        entry = LedgerEntry(
            ts=time.time(),
            day=self._today().isoformat(),
            provider=provider,
            kind=kind,
            units=units,
            usd=usd,
            run_id=run_id,
        )
        with self._lock, open(self._path, "a") as f:
            f.write(json.dumps(asdict(entry)) + "\n")
        return entry

    def over_cap(self, provider: str) -> bool:
        return self.spent_today(provider) >= self.cap(provider)


if __name__ == "__main__":
    import sys

    from harness.config import load_config

    cfg = load_config(sys.argv[1] if len(sys.argv) > 1 else "mac")
    ledger = CostLedger.from_providers_config(cfg.providers, cfg.path("data_dir") / "cost_ledger.jsonl")
    print(f"Spend on {datetime.now().date()} (USD, list price):")
    for name in cfg.providers["providers"]:
        print(f"  {name:<11} spent {ledger.spent_today(name):8.4f}  cap {ledger.cap(name):6.2f}")
