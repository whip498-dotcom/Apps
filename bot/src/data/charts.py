"""Premarket chart screenshot generator.

Renders a 5-minute candlestick chart with:
  - VWAP line
  - Premarket high (PMH) / low (PML) horizontal lines
  - Prior day high (PDH) / close (PDC) horizontal lines
  - Entry / stop / TP1 / TP2 zones (per-side)

Output: PNG bytes suitable for posting as a Discord webhook attachment.

Charts are an interpretation aid, not a trading signal. Don't trust the
levels in a fast market — verify on your IBKR chart before clicking buy.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Optional

import matplotlib

matplotlib.use("Agg")  # no display required
import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd
import yfinance as yf

from .levels import Levels


def _get_5m_bars(symbol: str) -> Optional[pd.DataFrame]:
    try:
        t = yf.Ticker(symbol)
        df = t.history(period="2d", interval="5m", prepost=True, auto_adjust=False)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    # Keep only last ~80 bars so the chart isn't cramped
    df = df.tail(80).copy()
    df.index = pd.to_datetime(df.index)
    return df[["Open", "High", "Low", "Close", "Volume"]]


def _vwap_series(df: pd.DataFrame) -> pd.Series:
    typical = (df["High"] + df["Low"] + df["Close"]) / 3
    cum_vol = df["Volume"].cumsum()
    cum_pv = (typical * df["Volume"]).cumsum()
    return (cum_pv / cum_vol).rename("VWAP")


def render_chart(symbol: str, side: str, levels: Optional[Levels]) -> Optional[bytes]:
    df = _get_5m_bars(symbol)
    if df is None or df.empty:
        return None

    addplots = []
    try:
        addplots.append(mpf.make_addplot(_vwap_series(df), color="#2E86DE", width=1.2))
    except Exception:
        pass

    hlines = {"hlines": [], "colors": [], "linestyle": "--", "linewidths": 1.0}

    def add_line(price: float, color: str):
        if price and price > 0:
            hlines["hlines"].append(float(price))
            hlines["colors"].append(color)

    if levels is not None:
        # Reference levels
        add_line(levels.premarket_high, "#F39C12")  # PMH amber
        add_line(levels.prior_day_high, "#34495E")  # PDH dark gray
        add_line(levels.prior_day_close, "#7F8C8D") # PDC gray
        # Trade plan
        if side == "long":
            add_line(levels.entry_low, "#27AE60")
            add_line(levels.entry_high, "#27AE60")
            add_line(levels.stop, "#C0392B")
            add_line(levels.target_1, "#16A085")
            add_line(levels.target_2, "#1ABC9C")
        else:  # short
            add_line(levels.entry_low, "#C0392B")
            add_line(levels.entry_high, "#C0392B")
            add_line(levels.stop, "#27AE60")
            add_line(levels.target_1, "#3498DB")
            add_line(levels.target_2, "#5DADE2")

    style = mpf.make_mpf_style(
        base_mpf_style="charles",
        rc={"font.size": 9, "axes.grid": True, "grid.alpha": 0.25},
        gridstyle="--",
    )

    title = f"${symbol}  ·  {side.upper()}"
    if levels is not None and levels.entry_low and levels.stop and levels.target_1:
        title += (
            f"   entry ${levels.entry_low:.2f}-{levels.entry_high:.2f} | "
            f"stop ${levels.stop:.2f} | "
            f"TP1 ${levels.target_1:.2f} (R:R {levels.rr_target_1:.2f})"
        )

    buf = io.BytesIO()
    try:
        mpf.plot(
            df,
            type="candle",
            volume=True,
            style=style,
            addplot=addplots if addplots else None,
            hlines=hlines if hlines["hlines"] else None,
            title=title,
            figsize=(10, 6),
            tight_layout=True,
            savefig=dict(fname=buf, format="png", dpi=110, bbox_inches="tight"),
        )
    except Exception:
        plt.close("all")
        return None
    plt.close("all")
    return buf.getvalue()
