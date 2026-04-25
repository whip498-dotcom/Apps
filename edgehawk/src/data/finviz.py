"""Finviz gainers/losers scraper.

Adds tickers to the universe even when they have no fresh PR/EDGAR signal —
catches sympathy plays, technical breakouts, parabolic shorts.

Free Finviz screener URLs:
  Top gainers: https://finviz.com/screener.ashx?v=111&s=ta_topgainers
  Top losers : https://finviz.com/screener.ashx?v=111&s=ta_toplosers

Finviz throttles aggressive scraping. We hit two URLs per cycle and
include a real User-Agent header.
"""
from __future__ import annotations

import re

import requests

GAINERS_URL = "https://finviz.com/screener.ashx?v=111&s=ta_topgainers"
LOSERS_URL = "https://finviz.com/screener.ashx?v=111&s=ta_toplosers"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
}

# Finviz row tickers appear in screener cells as quote.ashx?t=AAPL
_TICKER_RE = re.compile(r"quote\.ashx\?t=([A-Z]{1,5})\b")


def _fetch_tickers(url: str) -> list[str]:
    try:
        r = requests.get(url, headers=_HEADERS, timeout=10)
        if r.status_code != 200:
            return []
    except Exception:
        return []
    found = _TICKER_RE.findall(r.text)
    seen: set[str] = set()
    out: list[str] = []
    for t in found:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def fetch_gainers() -> list[str]:
    return _fetch_tickers(GAINERS_URL)


def fetch_losers() -> list[str]:
    return _fetch_tickers(LOSERS_URL)


def fetch_movers() -> dict[str, list[str]]:
    return {"gainers": fetch_gainers(), "losers": fetch_losers()}
