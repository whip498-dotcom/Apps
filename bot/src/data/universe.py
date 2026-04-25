"""Candidate ticker universe.

Sources (all free):
  1. User-edited watchlist.txt
  2. SEC EDGAR live filings (8-K / 424B5 / S-1 / etc)
  3. Finnhub general market news related-tickers
  4. PR wires (GlobeNewswire / BusinessWire / PR Newswire / Accesswire)
  5. Finviz top gainers + top losers (small-cap movers)

The set is filtered to plausible US small caps (1-5 alpha chars).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

import finnhub

from ..config import CONFIG
from .edgar import fetch_recent_filings, filings_by_ticker
from .finviz import fetch_gainers, fetch_losers
from .prwires import fetch_pr_items, pr_items_by_symbol

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
    return set(filings_by_ticker(fetch_recent_filings()).keys())


def _from_pr_wires() -> set[str]:
    return set(pr_items_by_symbol(fetch_pr_items(hours=12)).keys())


def _from_finviz() -> set[str]:
    return set(fetch_gainers()) | set(fetch_losers())


def build_universe(extra: Iterable[str] = ()) -> list[str]:
    syms: set[str] = set()
    syms.update(_read_watchlist())
    syms.update(_from_edgar())
    syms.update(_from_finnhub_market_news())
    syms.update(_from_pr_wires())
    syms.update(_from_finviz())
    syms.update(s.upper() for s in extra)
    return sorted(s for s in syms if 1 <= len(s) <= 5 and s.isalpha())
