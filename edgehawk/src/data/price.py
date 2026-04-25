"""Price + volume snapshots from yfinance.

yfinance is unofficial but free and gives premarket prices via 1m bars
on the current day. We use it as the primary source.
"""
from __future__ import annotations

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
    float_shares: int | None = None

    @property
    def relative_volume(self) -> float:
        if self.avg_volume_30d <= 0:
            return 0.0
        return self.premarket_volume / self.avg_volume_30d

    @property
    def float_rotation(self) -> float:
        """How many times the float has changed hands premarket. >1 is hot, >5 is parabolic."""
        if not self.float_shares or self.float_shares <= 0:
            return 0.0
        return self.premarket_volume / self.float_shares


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


def fetch_quotes(symbols: list[str]) -> dict[str, Quote]:
    """Bulk fetch with graceful failure on any single symbol."""
    out: dict[str, Quote] = {}
    for s in symbols:
        try:
            q = fetch_quote(s)
            if q is not None:
                out[s] = q
        except Exception:
            continue
    return out
