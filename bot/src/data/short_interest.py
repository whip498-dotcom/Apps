"""Short interest + days-to-cover scrape.

Source: stockanalysis.com (public, free). Cached on disk for 24h since
short interest only updates twice monthly.

For shorts: high SI%/DTC = squeeze risk, downsize.
For longs: high SI%/DTC = squeeze fuel, upsize on momentum days.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Optional

import requests
from bs4 import BeautifulSoup

from ..config import CONFIG

_CACHE_FILE = CONFIG.cache_dir / "short_interest_cache.json"
_CACHE_TTL = 24 * 3600

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
}


@dataclass
class ShortInterest:
    symbol: str
    short_interest_pct: float          # % of float
    days_to_cover: float
    short_shares: int
    fetched_at: float


def _load_cache() -> dict:
    if not _CACHE_FILE.exists():
        return {}
    try:
        return json.loads(_CACHE_FILE.read_text())
    except json.JSONDecodeError:
        return {}


def _save_cache(cache: dict) -> None:
    _CACHE_FILE.write_text(json.dumps(cache))


def _parse_number(text: str) -> Optional[float]:
    if not text:
        return None
    text = text.strip().replace(",", "").replace("%", "")
    if text.endswith("M"):
        try: return float(text[:-1]) * 1_000_000
        except ValueError: return None
    if text.endswith("K"):
        try: return float(text[:-1]) * 1_000
        except ValueError: return None
    if text.endswith("B"):
        try: return float(text[:-1]) * 1_000_000_000
        except ValueError: return None
    try: return float(text)
    except ValueError: return None


def _scrape(symbol: str) -> Optional[ShortInterest]:
    url = f"https://stockanalysis.com/stocks/{symbol.lower()}/statistics/"
    try:
        r = requests.get(url, headers=_HEADERS, timeout=10)
        if r.status_code != 200:
            return None
    except Exception:
        return None

    soup = BeautifulSoup(r.text, "lxml")
    si_pct = dtc = short_shares = None

    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        label = cells[0].get_text(strip=True).lower()
        value = cells[1].get_text(strip=True)
        if "short" in label and "%" in label and "float" in label:
            si_pct = _parse_number(value)
        elif "short" in label and "ratio" in label:
            dtc = _parse_number(value)
        elif label.startswith("short interest") and short_shares is None:
            short_shares = _parse_number(value)

    if si_pct is None and dtc is None and short_shares is None:
        return None

    return ShortInterest(
        symbol=symbol.upper(),
        short_interest_pct=si_pct or 0.0,
        days_to_cover=dtc or 0.0,
        short_shares=int(short_shares or 0),
        fetched_at=time.time(),
    )


def get_short_interest(symbol: str) -> Optional[ShortInterest]:
    cache = _load_cache()
    entry = cache.get(symbol.upper())
    now = time.time()
    if entry and (now - entry.get("fetched_at", 0) < _CACHE_TTL):
        return ShortInterest(**entry)

    si = _scrape(symbol)
    if si is None:
        return None
    cache[symbol.upper()] = si.__dict__
    _save_cache(cache)
    return si
