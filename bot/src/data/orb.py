"""Opening Range Breakout (ORB) levels and break detection.

The first N minutes after the cash open (default 5) define the opening range.
Breaking above the ORB high on volume = high-edge long continuation.
Breaking below the ORB low on volume = high-edge short continuation.

Times are in US Eastern (market time). Premarket activity does NOT count.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Optional

import pandas as pd
import pytz
import yfinance as yf

from ..config import CONFIG

NY = pytz.timezone("America/New_York")
MARKET_OPEN = time(9, 30)


@dataclass
class ORB:
    symbol: str
    high: float
    low: float
    range_dollars: float
    volume: int
    captured_at: datetime  # in NY tz


def _now_ny() -> datetime:
    return datetime.now(NY)


def _orb_window_complete(now_ny: datetime) -> bool:
    """True if the ORB window has fully elapsed since today's open."""
    if now_ny.weekday() >= 5:
        return False
    open_dt = NY.localize(datetime.combine(now_ny.date(), MARKET_OPEN))
    elapsed = (now_ny - open_dt).total_seconds() / 60
    return elapsed >= CONFIG.orb_minutes


def compute_orb(symbol: str) -> Optional[ORB]:
    """Returns the opening range for *today* once the window has closed."""
    now_ny = _now_ny()
    if not _orb_window_complete(now_ny):
        return None

    try:
        df = yf.Ticker(symbol).history(period="1d", interval="1m", prepost=False, auto_adjust=False)
    except Exception:
        return None
    if df is None or df.empty:
        return None

    # Convert index to NY tz
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC").tz_convert(NY)
    else:
        df.index = df.index.tz_convert(NY)

    today = now_ny.date()
    open_dt = NY.localize(datetime.combine(today, MARKET_OPEN))
    end_dt = open_dt + pd.Timedelta(minutes=CONFIG.orb_minutes)
    window = df[(df.index >= open_dt) & (df.index < end_dt)]
    if window.empty:
        return None

    return ORB(
        symbol=symbol.upper(),
        high=float(window["High"].max()),
        low=float(window["Low"].min()),
        range_dollars=float(window["High"].max() - window["Low"].min()),
        volume=int(window["Volume"].sum()),
        captured_at=now_ny,
    )


def detect_break(symbol: str, orb: ORB) -> Optional[str]:
    """Returns 'orb_break_up' / 'orb_break_down' / None.

    A break requires the latest 1-min bar to *close* outside the range, AND
    the bar's volume to be at least 1.5x the average bar volume during the
    range itself (filters out drift).
    """
    try:
        df = yf.Ticker(symbol).history(period="1d", interval="1m", prepost=False, auto_adjust=False)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC").tz_convert(NY)
    else:
        df.index = df.index.tz_convert(NY)

    today = orb.captured_at.date()
    end_dt = NY.localize(datetime.combine(today, MARKET_OPEN)) + pd.Timedelta(minutes=CONFIG.orb_minutes)
    after = df[df.index >= end_dt]
    if after.empty:
        return None

    last = after.iloc[-1]
    avg_range_vol = orb.volume / max(1, CONFIG.orb_minutes)
    vol_ok = last["Volume"] >= avg_range_vol * 1.5

    if last["Close"] > orb.high and vol_ok:
        return "orb_break_up"
    if last["Close"] < orb.low and vol_ok:
        return "orb_break_down"
    return None
