"""Runtime config loaded from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Config:
    finnhub_key: str = os.getenv("FINNHUB_API_KEY", "")
    discord_webhook: str = os.getenv("DISCORD_WEBHOOK_URL", "")
    polygon_key: str = os.getenv("POLYGON_API_KEY", "")
    sec_user_agent: str = os.getenv("SEC_USER_AGENT", "Trading Bot contact@example.com")

    price_min: float = _f("SCAN_PRICE_MIN", 3.0)
    price_max: float = _f("SCAN_PRICE_MAX", 20.0)
    max_float: int = _i("SCAN_MAX_FLOAT", 30_000_000)
    min_gap_pct: float = _f("SCAN_MIN_GAP_PCT", 10.0)
    min_premarket_volume: int = _i("SCAN_MIN_PREMARKET_VOLUME", 50_000)
    min_relative_volume: float = _f("SCAN_MIN_RELATIVE_VOLUME", 2.0)

    account_equity: float = _f("ACCOUNT_EQUITY", 800.0)
    max_risk_per_trade_pct: float = _f("MAX_RISK_PER_TRADE_PCT", 2.0)
    max_position_size_pct: float = _f("MAX_POSITION_SIZE_PCT", 25.0)

    # Re-alert thresholds for tickers already alerted this session.
    realert_price_pct: float = _f("REALERT_PRICE_PCT", 5.0)
    realert_volume_multiple: float = _f("REALERT_VOLUME_MULTIPLE", 2.0)
    realert_cooldown_seconds: int = _i("REALERT_COOLDOWN_SECONDS", 300)

    cache_dir: Path = ROOT / "data_cache"
    db_path: Path = ROOT / "journal.db"


CONFIG = Config()
CONFIG.cache_dir.mkdir(exist_ok=True)
