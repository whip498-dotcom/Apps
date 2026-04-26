"""Market-wide context for the Daily Briefing.

Three lightweight pulls so Claude has macro context when it picks the
day's longs and shorts:
    - Index session deltas: SPY, QQQ, IWM, VIX (last vs. prior close)
    - Top US market headlines from Finnhub general feed
    - Recent dilution filings from EDGAR (S-1 / S-3 / 424B*)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

import yfinance as yf

from ..config import CONFIG
from . import finnhub_pool
from .edgar import DILUTIVE_FORMS, fetch_recent_filings

_INDEX_TICKERS: tuple[str, ...] = ("SPY", "QQQ", "IWM", "^VIX")


@dataclass
class IndexSnapshot:
    symbol: str
    last: float
    prev_close: float

    @property
    def change_pct(self) -> float:
        if self.prev_close <= 0:
            return 0.0
        return (self.last - self.prev_close) / self.prev_close * 100.0


@dataclass
class Headline:
    headline: str
    source: str
    url: str
    published_at: datetime


@dataclass
class MarketContext:
    indices: list[IndexSnapshot]
    headlines: list[Headline]
    dilution_filings: list[str]   # already-formatted single-line strings
    fetched_at: datetime

    def index(self, symbol: str) -> IndexSnapshot | None:
        for s in self.indices:
            if s.symbol.upper() == symbol.upper():
                return s
        return None


def _fetch_index(symbol: str) -> IndexSnapshot | None:
    try:
        bars = yf.Ticker(symbol).history(period="5d", interval="1d", auto_adjust=False)
    except Exception:
        return None
    if bars is None or bars.empty or len(bars) < 2:
        return None
    last = float(bars["Close"].iloc[-1])
    prev = float(bars["Close"].iloc[-2])
    return IndexSnapshot(symbol=symbol.upper(), last=last, prev_close=prev)


def _fetch_headlines(limit: int = 10, hours: int = 12) -> list[Headline]:
    client = finnhub_pool.next_client()
    if client is None:
        return []
    try:
        raw = client.general_news("general", min_id=0)
    except Exception:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out: list[Headline] = []
    for r in raw:
        ts = datetime.fromtimestamp(r.get("datetime", 0), tz=timezone.utc)
        if ts < cutoff:
            continue
        out.append(Headline(
            headline=r.get("headline", "")[:200],
            source=r.get("source", ""),
            url=r.get("url", ""),
            published_at=ts,
        ))
    out.sort(key=lambda h: h.published_at, reverse=True)
    return out[:limit]


def _fetch_dilution_filings(limit: int = 10) -> list[str]:
    try:
        filings = fetch_recent_filings()
    except Exception:
        return []
    out: list[str] = []
    for f in filings:
        if f.form not in DILUTIVE_FORMS:
            continue
        ticker = f.ticker or "?"
        out.append(f"{ticker} · {f.form} · {f.company[:60]}")
        if len(out) >= limit:
            break
    return out


def gather_market_context() -> MarketContext:
    indices = [s for s in (_fetch_index(t) for t in _INDEX_TICKERS) if s is not None]
    return MarketContext(
        indices=indices,
        headlines=_fetch_headlines(),
        dilution_filings=_fetch_dilution_filings(),
        fetched_at=datetime.now(timezone.utc),
    )
