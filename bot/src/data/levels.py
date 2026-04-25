"""Key intraday levels for breakout/breakdown decisions.

Bullish-Bob-style "Key Levels" surface the lines a discretionary trader
actually watches in the first hour:

    PMH / PML  — premarket high & low (today before US RTH open)
    PDH / PDL  — prior day's regular-hours high & low
    ORH / ORL  — opening range (first 5 min of RTH) high & low
    PDC        — prior day's close

Computed from the 1-minute bars already pulled in price.fetch_quote so
no extra yfinance calls are required. Times are in US/Eastern because
that's the session the levels live in.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time as dtime
from typing import Optional

import pandas as pd

# US RTH 09:30-16:00 ET. yfinance returns tz-aware index in US/Eastern when
# prepost=True is requested, so naive comparisons against these times work
# after we localize the index.
_RTH_OPEN = dtime(9, 30)
_RTH_CLOSE = dtime(16, 0)
_OR_END = dtime(9, 35)  # 5-minute opening range


@dataclass
class KeyLevels:
    symbol: str
    pmh: Optional[float] = None
    pml: Optional[float] = None
    pdh: Optional[float] = None
    pdl: Optional[float] = None
    pdc: Optional[float] = None
    orh: Optional[float] = None
    orl: Optional[float] = None

    def near_pmh(self, last: float, tolerance_pct: float = 1.0) -> bool:
        if self.pmh is None or last <= 0:
            return False
        return abs(last - self.pmh) / last * 100.0 <= tolerance_pct

    def above_pmh(self, last: float) -> bool:
        return self.pmh is not None and last > self.pmh

    def above_pdh(self, last: float) -> bool:
        return self.pdh is not None and last > self.pdh


def _to_eastern(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    idx = df.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    return df.tz_convert("US/Eastern") if df.index.tz is not None else df.set_index(idx.tz_convert("US/Eastern"))


def compute_levels(symbol: str, intraday_1m: pd.DataFrame, daily: pd.DataFrame) -> KeyLevels:
    """Build KeyLevels from the same DataFrames price.fetch_quote already has.

    intraday_1m: 1-minute bars covering today + previous session (prepost=True)
    daily: daily bars for at least the last few sessions
    """
    out = KeyLevels(symbol=symbol.upper())

    if daily is not None and not daily.empty and len(daily) >= 2:
        prev = daily.iloc[-2]
        out.pdh = float(prev["High"])
        out.pdl = float(prev["Low"])
        out.pdc = float(prev["Close"])

    if intraday_1m is None or intraday_1m.empty:
        return out

    bars = _to_eastern(intraday_1m).copy()
    bars["session_date"] = bars.index.date
    bars["t"] = bars.index.time

    # Today is the latest session present
    today = bars["session_date"].max()
    today_bars = bars[bars["session_date"] == today]
    if today_bars.empty:
        return out

    pm = today_bars[today_bars["t"] < _RTH_OPEN]
    if not pm.empty:
        out.pmh = float(pm["High"].max())
        out.pml = float(pm["Low"].min())

    or_window = today_bars[(today_bars["t"] >= _RTH_OPEN) & (today_bars["t"] < _OR_END)]
    if not or_window.empty:
        out.orh = float(or_window["High"].max())
        out.orl = float(or_window["Low"].min())

    return out
