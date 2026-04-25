"""PR wire RSS aggregator: GlobeNewswire, BusinessWire, PR Newswire, Accesswire.

Press releases hit the wires *before* Finnhub indexes them. For small caps
this is often a 5-30s edge — meaningful when a low-float ticker is about to
run 50%.

We pull the public RSS feeds, regex out tickers from headline + summary
(matching `(NASDAQ: XYZ)` / `(NYSE: XYZ)` / `(OTC: XYZ)` / `(NYSEAMERICAN: XYZ)`),
and attach to news items the scanner already understands.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

import feedparser

PR_FEEDS = [
    # GlobeNewswire — public companies
    "https://www.globenewswire.com/RssFeed/orgclass/1/feedTitle/GlobeNewswire%20-%20News%20about%20Public%20Companies",
    # BusinessWire — public companies
    "https://feed.businesswire.com/rss/home/?rss=G1QFDERJXkJpDh1aWFNUUEdcXxBT",
    # PR Newswire — financial news
    "https://www.prnewswire.com/rss/financial-services-latest-news/financial-services-latest-news-list.rss",
    # Accesswire — small/microcap heavy
    "https://www.accesswire.com/api/rss.ashx",
]

_TICKER_RE = re.compile(
    r"\((?:NASDAQ|NYSE|NYSEAMERICAN|OTCQB|OTCQX|OTC|AMEX|CSE)\s*:\s*([A-Z]{1,5})\)",
    re.IGNORECASE,
)


@dataclass
class PRItem:
    symbol: str
    headline: str
    summary: str
    url: str
    source: str
    published_at: datetime


def _parse_time(entry) -> datetime:
    for key in ("published_parsed", "updated_parsed"):
        tm = entry.get(key)
        if tm:
            return datetime(*tm[:6], tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _extract_tickers(text: str) -> list[str]:
    return list({m.upper() for m in _TICKER_RE.findall(text)})


def fetch_pr_items(hours: int = 12, feeds: Iterable[str] = PR_FEEDS) -> list[PRItem]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out: list[PRItem] = []
    for url in feeds:
        try:
            feed = feedparser.parse(url)
        except Exception:
            continue
        source = feed.feed.get("title", url) if hasattr(feed, "feed") else url
        for entry in feed.entries:
            ts = _parse_time(entry)
            if ts < cutoff:
                continue
            headline = entry.get("title", "")
            summary = entry.get("summary", "") or entry.get("description", "")
            tickers = _extract_tickers(f"{headline} {summary}")
            if not tickers:
                continue
            for sym in tickers:
                out.append(PRItem(
                    symbol=sym,
                    headline=headline[:280],
                    summary=re.sub(r"<[^>]+>", "", summary)[:280],
                    url=entry.get("link", ""),
                    source=source,
                    published_at=ts,
                ))
    out.sort(key=lambda x: x.published_at, reverse=True)
    return out


def pr_items_by_symbol(items: list[PRItem]) -> dict[str, list[PRItem]]:
    by: dict[str, list[PRItem]] = {}
    for it in items:
        by.setdefault(it.symbol, []).append(it)
    return by
