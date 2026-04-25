"""Price + volume snapshots from yfinance.

yfinance is unofficial but free and gives premarket prices via 1m bars
on the current day. We use it as the primary source.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import yfinance as yf
from tenacity import retry, stop_after_attempt, wait_exponential


@dataclass
class Quote:
    symbol: str
    last: float
    prev_close: float
    premarket_volume: int
    avg_volume_30d: float
    gap_pct: float
    timestamp: datetime

    @property
    def relative_volume(self) -> float:
        if self.avg_volume_30d <= 0:
            return 0.0
        return self.premarket_volume / self.avg_volume_30d


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
def fetch_quote(symbol: str) -> Optional[Quote]:
    """Pull a premarket quote snapshot. Returns None if data is unusable."""
    t = yf.Ticker(symbol)
    # 2 days of 1m bars covers premarket of the current session
    intraday = t.history(period="2d", interval="1m", prepost=True, auto_adjust=False)
    if intraday.empty:
        return None

    daily = t.history(period="60d", interval="1d", auto_adjust=False)
    if daily.empty or len(daily) < 5:
        return None

    prev_close = float(daily["Close"].iloc[-2]) if len(daily) >= 2 else float(daily["Close"].iloc[-1])
    avg_vol_30d = float(daily["Volume"].tail(30).mean())

    today_utc = datetime.now(timezone.utc).date()
    today_bars = intraday[intraday.index.date == today_utc]
    if today_bars.empty:
        today_bars = intraday.tail(60)

    last = float(today_bars["Close"].iloc[-1])
    pm_vol = int(today_bars["Volume"].sum())
    gap_pct = ((last / prev_close) - 1.0) * 100.0 if prev_close else 0.0

    return Quote(
        symbol=symbol.upper(),
        last=last,
        prev_close=prev_close,
        premarket_volume=pm_vol,
        avg_volume_30d=avg_vol_30d,
        gap_pct=gap_pct,
        timestamp=datetime.now(timezone.utc),
    )


def fetch_quotes(symbols: list[str], max_workers: int = 8) -> dict[str, Quote]:
    """Bulk fetch with graceful failure on any single symbol.

    Runs in a thread pool — yfinance is I/O bound on HTTP calls, so each
    sequential fetch with retries was making the 24/7 scan loop drag long
    enough that later names in the universe never got their turn before the
    next iteration kicked off.
    """
    out: dict[str, Quote] = {}
    if not symbols:
        return out
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(fetch_quote, s): s for s in symbols}
        for fut in as_completed(futures):
            try:
                q = fut.result()
            except Exception:
                continue
            if q is not None:
                out[q.symbol] = q
    return out
