"""Candidate ticker universe for premarket scanning.

We combine three signals to find symbols worth fetching quotes for:

  1. Symbols mentioned in recent SEC EDGAR filings (catalyst-rich)
  2. Symbols mentioned in recent Finnhub general market news
  3. A persisted watchlist the user can edit (`watchlist.txt`)

This avoids paying for a full small-cap universe scanner while still
surfacing the names that actually move premarket.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import finnhub

from ..config import CONFIG
from .edgar import fetch_recent_filings, filings_by_ticker

WATCHLIST = CONFIG.cache_dir.parent / "watchlist.txt"


def _read_watchlist() -> set[str]:
    if not WATCHLIST.exists():
        WATCHLIST.write_text(
            "# One ticker per line. Lines starting with # are ignored.\n"
            "# Add symbols you want to scan every morning.\n"
        )
        return set()
    return {
        line.strip().upper()
        for line in WATCHLIST.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    }


def _from_finnhub_market_news(hours: int = 12) -> set[str]:
    if not CONFIG.finnhub_key:
        return set()
    client = finnhub.Client(api_key=CONFIG.finnhub_key)
    try:
        news = client.general_news("general", min_id=0)
    except Exception:
        return set()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    syms: set[str] = set()
    for n in news:
        ts = datetime.fromtimestamp(n.get("datetime", 0), tz=timezone.utc)
        if ts < cutoff:
            continue
        related = n.get("related", "") or ""
        for s in related.split(","):
            s = s.strip().upper()
            if s and s.isascii() and 1 <= len(s) <= 5 and s.isalpha():
                syms.add(s)
    return syms


def _from_edgar() -> set[str]:
    filings = fetch_recent_filings()
    return set(filings_by_ticker(filings).keys())


def build_universe(extra: Iterable[str] = ()) -> list[str]:
    syms = set()
    syms.update(_read_watchlist())
    syms.update(_from_edgar())
    syms.update(_from_finnhub_market_news())
    syms.update(s.upper() for s in extra)
    # Shuffle so each scan iteration samples the universe in a different order.
    # Sorting alphabetically biased the top-N toward A-D names whenever scores
    # tied (stable sort) and starved late-alphabet names of Finnhub rate budget.
    out = [s for s in syms if 1 <= len(s) <= 5 and s.isalpha()]
    random.shuffle(out)
    return out
