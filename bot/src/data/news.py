"""News catalyst lookups via Finnhub.

Finnhub's free tier gives 60 calls/min and real-time US company news.
We pull the last 24h of news per symbol and tag catalyst keywords.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import finnhub

from ..config import CONFIG

# Keywords commonly associated with explosive small-cap moves.
# Order matters loosely — earlier = stronger signal.
CATALYST_KEYWORDS = [
    ("fda approval", "FDA"),
    ("phase 3", "PHASE3"),
    ("phase 2", "PHASE2"),
    ("merger", "MERGER"),
    ("acquisition", "ACQUISITION"),
    ("buyout", "BUYOUT"),
    ("partnership", "PARTNERSHIP"),
    ("contract", "CONTRACT"),
    ("uplisting", "UPLISTING"),
    ("offering", "DILUTION"),
    ("reverse split", "REVERSE_SPLIT"),
    ("earnings", "EARNINGS"),
    ("guidance", "GUIDANCE"),
    ("clinical trial", "CLINICAL"),
    ("patent", "PATENT"),
    ("breakthrough", "BREAKTHROUGH"),
    ("squeeze", "SQUEEZE"),
    ("short interest", "SHORT_INTEREST"),
]


@dataclass
class NewsItem:
    symbol: str
    headline: str
    summary: str
    url: str
    source: str
    published_at: datetime
    tags: list[str]

    @property
    def is_dilutive(self) -> bool:
        return "DILUTION" in self.tags or "REVERSE_SPLIT" in self.tags


def _classify(text: str) -> list[str]:
    t = text.lower()
    return [tag for kw, tag in CATALYST_KEYWORDS if kw in t]


def _client() -> Optional[finnhub.Client]:
    if not CONFIG.finnhub_key:
        return None
    return finnhub.Client(api_key=CONFIG.finnhub_key)


def fetch_recent_news(symbol: str, hours: int = 24) -> list[NewsItem]:
    client = _client()
    if client is None:
        return []
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=max(1, hours // 24))
    try:
        raw = client.company_news(symbol, _from=start.isoformat(), to=end.isoformat())
    except Exception:
        return []

    items: list[NewsItem] = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    for r in raw:
        ts = datetime.fromtimestamp(r.get("datetime", 0), tz=timezone.utc)
        if ts < cutoff:
            continue
        text = f"{r.get('headline', '')} {r.get('summary', '')}"
        tags = _classify(text)
        items.append(
            NewsItem(
                symbol=symbol.upper(),
                headline=r.get("headline", ""),
                summary=r.get("summary", "")[:280],
                url=r.get("url", ""),
                source=r.get("source", ""),
                published_at=ts,
                tags=tags,
            )
        )
    items.sort(key=lambda x: x.published_at, reverse=True)
    return items


def has_catalyst(symbol: str, hours: int = 24) -> tuple[bool, list[NewsItem]]:
    items = fetch_recent_news(symbol, hours=hours)
    tagged = [i for i in items if i.tags]
    return bool(tagged), tagged
