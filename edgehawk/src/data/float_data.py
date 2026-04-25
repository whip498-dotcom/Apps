"""Float lookup with on-disk cache.

Float doesn't change often, so we cache aggressively (7 days). yfinance
exposes `floatShares` on `Ticker.info`, which is rate-limited but fine
for batch refresh.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import yfinance as yf

from ..config import CONFIG

_CACHE_FILE = CONFIG.cache_dir / "float_cache.json"
_CACHE_TTL_SECONDS = 7 * 24 * 3600


def _load_cache() -> dict:
    if not _CACHE_FILE.exists():
        return {}
    try:
        return json.loads(_CACHE_FILE.read_text())
    except json.JSONDecodeError:
        return {}


def _save_cache(cache: dict) -> None:
    _CACHE_FILE.write_text(json.dumps(cache))


def get_float(symbol: str, force_refresh: bool = False) -> Optional[int]:
    """Returns float shares for `symbol`, or None if unknown."""
    symbol = symbol.upper()
    cache = _load_cache()
    entry = cache.get(symbol)
    now = time.time()

    if not force_refresh and entry and (now - entry["fetched_at"] < _CACHE_TTL_SECONDS):
        return entry.get("float")

    try:
        info = yf.Ticker(symbol).info
        float_shares = info.get("floatShares")
        if float_shares is None:
            float_shares = info.get("sharesOutstanding")
        cache[symbol] = {"float": float_shares, "fetched_at": now}
        _save_cache(cache)
        return float_shares
    except Exception:
        return entry.get("float") if entry else None


def get_floats(symbols: list[str]) -> dict[str, Optional[int]]:
    return {s: get_float(s) for s in symbols}
