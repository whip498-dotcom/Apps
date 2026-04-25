"""Price levels: entry/SL/TP zones, pivots, VWAP, support/resistance.

Computed per candidate from yfinance intraday + daily bars. All values in
dollars. The trade plan respects direction:

  LONG:
    entry zone   = breakout of premarket high (PMH) ± buffer
    stop         = max(VWAP, recent intraday low) shifted down a tick
    target 1     = nearest resistance (PDH / R1 / round) above entry
    target 2     = next resistance after that

  SHORT:
    entry zone   = rejection band just below PMH (or break of PML)
    stop         = above PMH (a few ticks)
    target 1     = nearest support (VWAP / PDC / S1 / round) below entry
    target 2     = next support after that

Risk:reward computed for TP1 and TP2 vs the entry midpoint.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import yfinance as yf
from tenacity import retry, stop_after_attempt, wait_exponential


@dataclass
class Levels:
    side: str  # 'long' | 'short'

    entry_low: float
    entry_high: float
    stop: float
    target_1: float
    target_2: float
    rr_target_1: float
    rr_target_2: float

    premarket_high: float
    premarket_low: float
    prior_day_high: float
    prior_day_low: float
    prior_day_close: float

    pivot: float
    r1: float
    r2: float
    s1: float
    s2: float
    vwap: float

    near_resistance: float
    near_support: float

    @property
    def entry_mid(self) -> float:
        return (self.entry_low + self.entry_high) / 2

    @property
    def risk_per_share(self) -> float:
        return abs(self.entry_mid - self.stop)


def _round_levels(price: float) -> list[float]:
    """Generate nearby round-number levels small caps respect."""
    if price <= 0:
        return []
    increments = [0.50, 1.00, 5.00, 10.00]
    out: set[float] = set()
    for inc in increments:
        below = math.floor(price / inc) * inc
        above = math.ceil(price / inc) * inc
        for v in (below, above, below - inc, above + inc):
            if v > 0:
                out.add(round(v, 2))
    return sorted(out)


def _premarket_session_today(intraday: pd.DataFrame) -> pd.DataFrame:
    if intraday.empty:
        return intraday
    today = datetime.now(timezone.utc).date()
    today_bars = intraday[intraday.index.date == today]
    if today_bars.empty:
        # Fall back to last trading day in the frame
        last_date = intraday.index.date.max()
        today_bars = intraday[intraday.index.date == last_date]
    return today_bars


def _vwap(bars: pd.DataFrame) -> float:
    if bars.empty or bars["Volume"].sum() == 0:
        return float("nan")
    typical = (bars["High"] + bars["Low"] + bars["Close"]) / 3
    return float((typical * bars["Volume"]).sum() / bars["Volume"].sum())


def _next_above(price: float, levels: list[float]) -> Optional[float]:
    for lv in sorted(levels):
        if lv > price:
            return lv
    return None


def _next_below(price: float, levels: list[float]) -> Optional[float]:
    for lv in sorted(levels, reverse=True):
        if lv < price:
            return lv
    return None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
def _fetch_bars(symbol: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    t = yf.Ticker(symbol)
    intraday = t.history(period="2d", interval="1m", prepost=True, auto_adjust=False)
    daily = t.history(period="60d", interval="1d", auto_adjust=False)
    return intraday, daily


def compute_levels(symbol: str, side: str, last_price: float) -> Optional[Levels]:
    """Returns a Levels object for the given side, or None if data is unusable."""
    if side not in ("long", "short"):
        raise ValueError(f"side must be 'long' or 'short', got {side!r}")

    try:
        intraday, daily = _fetch_bars(symbol)
    except Exception:
        return None
    if intraday.empty or daily.empty or len(daily) < 2:
        return None

    today_bars = _premarket_session_today(intraday)
    if today_bars.empty:
        return None

    pmh = float(today_bars["High"].max())
    pml = float(today_bars["Low"].min())
    pdh = float(daily["High"].iloc[-2])
    pdl = float(daily["Low"].iloc[-2])
    pdc = float(daily["Close"].iloc[-2])

    pivot = (pdh + pdl + pdc) / 3
    r1 = 2 * pivot - pdl
    r2 = pivot + (pdh - pdl)
    s1 = 2 * pivot - pdh
    s2 = pivot - (pdh - pdl)
    vwap = _vwap(today_bars)

    rounds = _round_levels(last_price)

    if side == "long":
        # Entry: break of PMH with a 0.3-1.5% buffer band
        entry_low = round(pmh + 0.01, 2)
        entry_high = round(pmh * 1.015, 2)
        # Stop: prefer VWAP support; fall back to recent intraday low
        recent_low = float(today_bars["Low"].tail(15).min())
        stop_candidate = max(
            vwap if not math.isnan(vwap) else recent_low,
            recent_low,
        )
        # Cap risk at ~6% of entry to avoid giant stops on volatile names
        max_risk = entry_low * 0.06
        stop = max(stop_candidate, entry_low - max_risk)
        stop = round(stop - 0.01, 2)

        # Resistance candidates above entry
        resistance_candidates = [pdh, r1, r2] + [r for r in rounds if r > entry_high]
        resistance_candidates = sorted({round(c, 2) for c in resistance_candidates if c > entry_high})

        target_1 = resistance_candidates[0] if resistance_candidates else round(entry_high * 1.05, 2)
        target_2 = resistance_candidates[1] if len(resistance_candidates) > 1 else round(target_1 * 1.05, 2)
        near_resistance = target_1
        near_support = stop

    else:  # short
        # Entry: just below PMH (rejection band)
        entry_high = round(pmh - 0.01, 2)
        entry_low = round(pmh * 0.985, 2)
        # Stop: above PMH with a small buffer
        max_risk = entry_high * 0.06
        stop = round(pmh + max(0.05, pmh * 0.015), 2)
        if stop - entry_high > max_risk:
            stop = round(entry_high + max_risk, 2)

        # Support candidates below entry
        support_candidates = [vwap, pdc, pdl, s1, s2] + [r for r in rounds if r < entry_low]
        support_candidates = [c for c in support_candidates if c is not None and not (isinstance(c, float) and math.isnan(c))]
        support_candidates = sorted({round(c, 2) for c in support_candidates if c < entry_low}, reverse=True)

        target_1 = support_candidates[0] if support_candidates else round(entry_low * 0.95, 2)
        target_2 = support_candidates[1] if len(support_candidates) > 1 else round(target_1 * 0.95, 2)
        near_support = target_1
        near_resistance = stop

    entry_mid = (entry_low + entry_high) / 2
    risk = abs(entry_mid - stop)
    rr1 = abs(target_1 - entry_mid) / risk if risk else 0.0
    rr2 = abs(target_2 - entry_mid) / risk if risk else 0.0

    return Levels(
        side=side,
        entry_low=entry_low,
        entry_high=entry_high,
        stop=stop,
        target_1=target_1,
        target_2=target_2,
        rr_target_1=round(rr1, 2),
        rr_target_2=round(rr2, 2),
        premarket_high=round(pmh, 2),
        premarket_low=round(pml, 2),
        prior_day_high=round(pdh, 2),
        prior_day_low=round(pdl, 2),
        prior_day_close=round(pdc, 2),
        pivot=round(pivot, 2),
        r1=round(r1, 2),
        r2=round(r2, 2),
        s1=round(s1, 2),
        s2=round(s2, 2),
        vwap=round(vwap, 2) if not math.isnan(vwap) else 0.0,
        near_resistance=round(near_resistance, 2),
        near_support=round(near_support, 2),
    )
