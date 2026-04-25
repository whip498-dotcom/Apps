"""Key intraday levels + leg structure + multi-timeframe alignment.

This is the data backing EdgeHawk's SQUEEZE ALERT view, modelled on
Bullish Bob's "Squeeze Potential / Key Levels" desk:

  Key levels (price reference)
    PMH / PML  — premarket high & low (today before US RTH open)
    PDH / PDL  — prior day's regular-hours high & low
    ORH / ORL  — opening range (first 5 min of RTH) high & low
    PDC        — prior day's close

  Leg levels (intraday structure for stops on momentum entries)
    L1 low     — most recent 3-bar pivot low after the day's high
    L2 low     — the pivot before L1 (the prior leg's launch)

  MTF (trend alignment, long-only)
    1m / 5m / 15m bullish flags = last close > 9-EMA AND EMA rising

Computed from the same 1-minute and daily bars price.fetch_quote
already has on hand — no extra yfinance calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time as dtime
from typing import Optional

import pandas as pd

# US RTH 09:30-16:00 ET. yfinance returns tz-aware index in US/Eastern when
# prepost=True is requested, so naive comparisons against these times work
# after we localize the index.
_RTH_OPEN = dtime(9, 30)
_RTH_CLOSE = dtime(16, 0)
_OR_END = dtime(9, 35)  # 5-minute opening range

# How many recent 1m bars to look across when finding the most recent leg
# pullback pivots. ~120 bars = 2 hours, enough for premarket + early RTH.
_LEG_LOOKBACK_BARS = 120
_EMA_LEN = 9


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

    # Leg structure for momentum-long stops
    leg1_low: Optional[float] = None  # most recent pullback pivot low
    leg2_low: Optional[float] = None  # the leg before that

    # Multi-timeframe trend (long-only): True bullish, False bearish, None unknown
    mtf_1m_bull: Optional[bool] = None
    mtf_5m_bull: Optional[bool] = None
    mtf_15m_bull: Optional[bool] = None

    def near_pmh(self, last: float, tolerance_pct: float = 1.0) -> bool:
        if self.pmh is None or last <= 0:
            return False
        return abs(last - self.pmh) / last * 100.0 <= tolerance_pct

    def above_pmh(self, last: float) -> bool:
        return self.pmh is not None and last > self.pmh

    def above_pdh(self, last: float) -> bool:
        return self.pdh is not None and last > self.pdh

    @property
    def mtf_alignment(self) -> int:
        """0-3 count of bullish timeframes (1m, 5m, 15m)."""
        return sum(
            1 for b in (self.mtf_1m_bull, self.mtf_5m_bull, self.mtf_15m_bull)
            if b is True
        )


def _to_eastern(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    idx = df.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    return df.tz_convert("US/Eastern") if df.index.tz is not None else df.set_index(idx.tz_convert("US/Eastern"))


def _resample(bars: pd.DataFrame, rule: str) -> pd.DataFrame:
    """OHLCV resample (rule like '5min', '15min'). Drops empty buckets."""
    if bars is None or bars.empty:
        return bars
    agg = bars.resample(rule).agg({
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum",
    })
    return agg.dropna(subset=["Close"])


def _is_mtf_bull(bars: pd.DataFrame) -> Optional[bool]:
    """True if last close > 9-EMA AND EMA[-1] > EMA[-3] (rising). None if insufficient data."""
    if bars is None or bars.empty or len(bars) < _EMA_LEN + 3:
        return None
    closes = bars["Close"].astype(float)
    ema = closes.ewm(span=_EMA_LEN, adjust=False).mean()
    last_close = float(closes.iloc[-1])
    return bool(last_close > float(ema.iloc[-1]) and float(ema.iloc[-1]) > float(ema.iloc[-3]))


def _find_leg_lows(bars: pd.DataFrame, n: int = 2) -> list[float]:
    """Most recent N 3-bar pivot lows in the lookback window.

    A pivot low is a bar whose Low is strictly lower than its immediate
    neighbours on both sides. Returned newest-first.
    """
    if bars is None or len(bars) < 5:
        return []
    recent = bars.tail(_LEG_LOOKBACK_BARS)
    lows = recent["Low"].astype(float).values
    pivots: list[float] = []
    for i in range(1, len(lows) - 1):
        if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
            pivots.append(float(lows[i]))
    if not pivots:
        return []
    return list(reversed(pivots))[:n]


def compute_levels(symbol: str, intraday_1m: pd.DataFrame, daily: pd.DataFrame) -> KeyLevels:
    """Build KeyLevels from the bars price.fetch_quote already has."""
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

    # Leg lows: only count pullback pivots that come AFTER the session high,
    # so L1 = the latest higher low you'd actually use as a momentum stop.
    if not today_bars.empty:
        high_idx = today_bars["High"].idxmax()
        post_high = today_bars.loc[high_idx:]
        legs = _find_leg_lows(post_high, n=2)
        if legs:
            out.leg1_low = legs[0]
            if len(legs) > 1:
                out.leg2_low = legs[1]

    # MTF trend on 1m / 5m / 15m. Long-bias only — we only care if trend is up.
    bars_only_ohlcv = today_bars[["Open", "High", "Low", "Close", "Volume"]]
    out.mtf_1m_bull = _is_mtf_bull(bars_only_ohlcv)
    out.mtf_5m_bull = _is_mtf_bull(_resample(bars_only_ohlcv, "5min"))
    out.mtf_15m_bull = _is_mtf_bull(_resample(bars_only_ohlcv, "15min"))

    return out
