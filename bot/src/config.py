"""Runtime config loaded from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _f(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw not in (None, "") else default


def _i(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


def _finnhub_keys() -> tuple[str, ...]:
    """Collect every Finnhub key the user has provided.

    Accepts any of:
      FINNHUB_API_KEY=abc
      FINNHUB_API_KEY_2=def              (also _3, _4, ...)
      FINNHUB_API_KEYS=abc,def,ghi       (comma-separated convenience)

    Each free key adds 60 calls/min of headroom — round-robin lets the
    scanner cover more of the universe per pass before catalyst lookups
    start getting rate-limited.
    """
    raw: list[str] = []
    primary = os.getenv("FINNHUB_API_KEY", "").strip()
    if primary:
        raw.append(primary)
    for i in range(2, 10):
        v = os.getenv(f"FINNHUB_API_KEY_{i}", "").strip()
        if v:
            raw.append(v)
    bulk = os.getenv("FINNHUB_API_KEYS", "")
    if bulk:
        raw.extend(s.strip() for s in bulk.split(",") if s.strip())
    # Preserve order, dedupe
    seen: set[str] = set()
    out: list[str] = []
    for k in raw:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return tuple(out)


@dataclass(frozen=True)
class Config:
    finnhub_keys: tuple[str, ...] = field(default_factory=_finnhub_keys)
    discord_webhook: str = os.getenv("DISCORD_WEBHOOK_URL", "")
    polygon_key: str = os.getenv("POLYGON_API_KEY", "")
    sec_user_agent: str = os.getenv("SEC_USER_AGENT", "Trading Bot contact@example.com")

    price_min: float = _f("SCAN_PRICE_MIN", 3.0)
    price_max: float = _f("SCAN_PRICE_MAX", 20.0)
    max_float: int = _i("SCAN_MAX_FLOAT", 30_000_000)
    min_gap_pct: float = _f("SCAN_MIN_GAP_PCT", 10.0)
    min_premarket_volume: int = _i("SCAN_MIN_PREMARKET_VOLUME", 50_000)
    min_relative_volume: float = _f("SCAN_MIN_RELATIVE_VOLUME", 2.0)

    # Squeeze-style scoring knobs (Bullish Bob style: low float + high SI + catalyst)
    min_short_interest_pct: float = _f("SCAN_MIN_SHORT_INTEREST_PCT", 0.0)
    min_confidence: int = _i("SCAN_MIN_CONFIDENCE", 8)

    account_equity: float = _f("ACCOUNT_EQUITY", 800.0)
    max_risk_per_trade_pct: float = _f("MAX_RISK_PER_TRADE_PCT", 2.0)
    max_position_size_pct: float = _f("MAX_POSITION_SIZE_PCT", 25.0)

    cache_dir: Path = ROOT / "data_cache"
    db_path: Path = ROOT / "journal.db"

    @property
    def finnhub_key(self) -> str:
        """First key — kept for callers that don't need rotation."""
        return self.finnhub_keys[0] if self.finnhub_keys else ""


CONFIG = Config()
CONFIG.cache_dir.mkdir(exist_ok=True)
