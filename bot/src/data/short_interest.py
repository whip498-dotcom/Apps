"""Short interest + days-to-cover lookup.

The squeeze half of "Squeeze Potential / Key Levels" needs three numbers:
    - short % of float
    - shares short
    - days to cover (short ratio)

Short interest is reported twice a month by FINRA, so a 24-hour disk cache
is plenty. yfinance surfaces all three under Ticker.info — no paid feed
needed for the free version of this scanner.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Optional

import yfinance as yf

from ..config import CONFIG

_CACHE_FILE = CONFIG.cache_dir / "short_interest_cache.json"
_CACHE_TTL_SECONDS = 24 * 3600


@dataclass
class ShortInterest:
    symbol: str
    short_pct_float: Optional[float]   # e.g. 0.32 = 32%
    shares_short: Optional[int]
    days_to_cover: Optional[float]

    @property
    def is_squeeze_candidate(self) -> bool:
        """Bullish-Bob-style threshold: SI ≥ 20% OR DTC ≥ 5."""
        if self.short_pct_float is not None and self.short_pct_float >= 0.20:
            return True
        if self.days_to_cover is not None and self.days_to_cover >= 5.0:
            return True
        return False


def _load_cache() -> dict:
    if not _CACHE_FILE.exists():
        return {}
    try:
        return json.loads(_CACHE_FILE.read_text())
    except json.JSONDecodeError:
        return {}


def _save_cache(cache: dict) -> None:
    _CACHE_FILE.write_text(json.dumps(cache))


def _from_cache_entry(symbol: str, entry: dict) -> ShortInterest:
    return ShortInterest(
        symbol=symbol,
        short_pct_float=entry.get("short_pct_float"),
        shares_short=entry.get("shares_short"),
        days_to_cover=entry.get("days_to_cover"),
    )


def get_short_interest(symbol: str, force_refresh: bool = False) -> ShortInterest:
    symbol = symbol.upper()
    cache = _load_cache()
    entry = cache.get(symbol)
    now = time.time()

    if not force_refresh and entry and (now - entry.get("fetched_at", 0) < _CACHE_TTL_SECONDS):
        return _from_cache_entry(symbol, entry)

    try:
        info = yf.Ticker(symbol).info
        short_pct = info.get("shortPercentOfFloat")
        shares_short = info.get("sharesShort")
        dtc = info.get("shortRatio")  # days to cover
        cache[symbol] = {
            "short_pct_float": float(short_pct) if short_pct is not None else None,
            "shares_short": int(shares_short) if shares_short is not None else None,
            "days_to_cover": float(dtc) if dtc is not None else None,
            "fetched_at": now,
        }
        _save_cache(cache)
        return _from_cache_entry(symbol, cache[symbol])
    except Exception:
        if entry:
            return _from_cache_entry(symbol, entry)
        return ShortInterest(symbol=symbol, short_pct_float=None,
                             shares_short=None, days_to_cover=None)
