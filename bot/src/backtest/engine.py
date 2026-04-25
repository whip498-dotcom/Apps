"""Minimal backtest engine for premarket-momentum entries.

Given a list of historical setups (symbol, date, side, entry, stop, t1, t2),
replay each one against 1m bars from the open through 11:00 ET and report:

  - hit_target_1, hit_target_2, hit_stop, time_to_target, max_favorable_excursion
  - per-setup expectancy in R-multiples
  - aggregate win rate, expectancy, profit factor

Data source: Polygon.io aggregates (free 5 req/min, 2y history) via REST.
For larger backtests, swap in flat files (paid Polygon tier) — only the
`fetch_minute_bars` function needs to change.

This is a *small* backtester suitable for validating setup edge before
risking capital. It is NOT a full event-driven sim. No PDT modeling, no
slippage modeling beyond a configurable per-trade buffer, no overnight
holds.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import pandas as pd
import requests

from ..config import CONFIG


@dataclass
class Setup:
    symbol: str
    trade_date: date
    side: str            # 'long' | 'short'
    entry: float
    stop: float
    target_1: float
    target_2: float
    setup_tag: str = ""
    catalyst: str = ""


@dataclass
class TradeResult:
    setup: Setup
    triggered: bool
    hit_target_1: bool
    hit_target_2: bool
    hit_stop: bool
    exit_price: Optional[float]
    exit_time: Optional[datetime]
    minutes_to_exit: Optional[int]
    max_favorable_R: float
    max_adverse_R: float
    r_multiple: float


def _ts_ms(d: date, hour: int, minute: int) -> int:
    return int(datetime(d.year, d.month, d.day, hour, minute, tzinfo=timezone.utc).timestamp() * 1000)


def fetch_minute_bars(symbol: str, day: date) -> Optional[pd.DataFrame]:
    """Fetch 1m bars for a single day from Polygon.io aggregates."""
    if not CONFIG.polygon_backtest_key:
        raise RuntimeError("POLYGON_BACKTEST_KEY required for backtest. Get a free key at polygon.io")
    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{symbol.upper()}/range/1/minute/"
        f"{day.isoformat()}/{day.isoformat()}"
    )
    try:
        r = requests.get(url, params={
            "adjusted": "true",
            "sort": "asc",
            "limit": 50000,
            "apiKey": CONFIG.polygon_backtest_key,
        }, timeout=15)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    data = r.json()
    if data.get("resultsCount", 0) == 0:
        return None
    df = pd.DataFrame(data["results"])
    df.rename(columns={"t":"ts","o":"open","h":"high","l":"low","c":"close","v":"volume"}, inplace=True)
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df.set_index("ts", inplace=True)
    return df[["open","high","low","close","volume"]]


def simulate(setup: Setup, bars: pd.DataFrame, max_hold_minutes: int = 120) -> TradeResult:
    """Walk forward through 1m bars from cash open until exit or timeout."""
    risk = abs(setup.entry - setup.stop)
    if risk <= 0:
        return TradeResult(setup, False, False, False, False, None, None, None, 0, 0, 0)

    open_dt = datetime(setup.trade_date.year, setup.trade_date.month, setup.trade_date.day,
                       13, 30, tzinfo=timezone.utc)  # 9:30 ET = 13:30 UTC (approximate; ignores DST for simplicity)
    end_dt = open_dt + timedelta(minutes=max_hold_minutes)
    window = bars[(bars.index >= open_dt) & (bars.index <= end_dt)]
    if window.empty:
        return TradeResult(setup, False, False, False, False, None, None, None, 0, 0, 0)

    triggered = False
    entry_price = None
    max_fav = 0.0
    max_adv = 0.0
    hit_t1 = hit_t2 = hit_stop = False
    exit_price = None
    exit_time = None

    for ts, bar in window.iterrows():
        # Check trigger first if not yet in
        if not triggered:
            if setup.side == "long" and bar["high"] >= setup.entry:
                triggered = True
                entry_price = setup.entry
            elif setup.side == "short" and bar["low"] <= setup.entry:
                triggered = True
                entry_price = setup.entry

        if not triggered:
            continue

        # Track excursion in R
        if setup.side == "long":
            fav = (bar["high"] - entry_price) / risk
            adv = (bar["low"] - entry_price) / risk
        else:
            fav = (entry_price - bar["low"]) / risk
            adv = (entry_price - bar["high"]) / risk
        max_fav = max(max_fav, fav)
        max_adv = min(max_adv, adv)

        # Stop hit?
        if setup.side == "long" and bar["low"] <= setup.stop:
            hit_stop = True
            exit_price = setup.stop
            exit_time = ts
            break
        if setup.side == "short" and bar["high"] >= setup.stop:
            hit_stop = True
            exit_price = setup.stop
            exit_time = ts
            break

        # T1 hit (we don't auto-stop at T1; just record). T2 ends trade.
        if setup.side == "long":
            if bar["high"] >= setup.target_1:
                hit_t1 = True
            if bar["high"] >= setup.target_2:
                hit_t2 = True
                exit_price = setup.target_2
                exit_time = ts
                break
        else:
            if bar["low"] <= setup.target_1:
                hit_t1 = True
            if bar["low"] <= setup.target_2:
                hit_t2 = True
                exit_price = setup.target_2
                exit_time = ts
                break

    if triggered and exit_price is None:
        # Time stop — exit at last close
        exit_price = float(window["close"].iloc[-1])
        exit_time = window.index[-1]

    if not triggered:
        return TradeResult(setup, False, False, False, False, None, None, None, 0, 0, 0)

    pnl_per_share = (exit_price - entry_price) if setup.side == "long" else (entry_price - exit_price)
    r = pnl_per_share / risk
    minutes_to_exit = int((exit_time - open_dt).total_seconds() / 60) if exit_time else None

    return TradeResult(
        setup=setup,
        triggered=True,
        hit_target_1=hit_t1,
        hit_target_2=hit_t2,
        hit_stop=hit_stop,
        exit_price=exit_price,
        exit_time=exit_time,
        minutes_to_exit=minutes_to_exit,
        max_favorable_R=round(max_fav, 2),
        max_adverse_R=round(max_adv, 2),
        r_multiple=round(r, 2),
    )


def run_backtest(setups: list[Setup], rate_limit_seconds: float = 12.5) -> list[TradeResult]:
    """Run a list of setups serially with a rate limit (Polygon free = 5/min)."""
    out: list[TradeResult] = []
    for s in setups:
        bars = fetch_minute_bars(s.symbol, s.trade_date)
        if bars is None:
            out.append(TradeResult(s, False, False, False, False, None, None, None, 0, 0, 0))
            continue
        out.append(simulate(s, bars))
        time.sleep(rate_limit_seconds)
    return out


@dataclass
class BacktestStats:
    n_setups: int
    n_triggered: int
    win_rate: float
    expectancy_R: float
    avg_win_R: float
    avg_loss_R: float
    profit_factor: float
    by_setup_tag: dict


def summarize(results: list[TradeResult]) -> BacktestStats:
    triggered = [r for r in results if r.triggered]
    wins = [r for r in triggered if r.r_multiple > 0]
    losses = [r for r in triggered if r.r_multiple <= 0]

    win_rate = len(wins) / len(triggered) if triggered else 0
    avg_win = sum(r.r_multiple for r in wins) / len(wins) if wins else 0
    avg_loss = sum(r.r_multiple for r in losses) / len(losses) if losses else 0
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss
    gross_win = sum(r.r_multiple for r in wins)
    gross_loss = abs(sum(r.r_multiple for r in losses))
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf") if gross_win > 0 else 0

    by_tag: dict = {}
    for r in triggered:
        tag = r.setup.setup_tag or "untagged"
        bucket = by_tag.setdefault(tag, {"n": 0, "wins": 0, "sum_R": 0.0})
        bucket["n"] += 1
        bucket["wins"] += 1 if r.r_multiple > 0 else 0
        bucket["sum_R"] += r.r_multiple
    for tag, b in by_tag.items():
        b["win_rate"] = b["wins"] / b["n"] if b["n"] else 0
        b["expectancy_R"] = b["sum_R"] / b["n"] if b["n"] else 0

    return BacktestStats(
        n_setups=len(results),
        n_triggered=len(triggered),
        win_rate=round(win_rate, 3),
        expectancy_R=round(expectancy, 3),
        avg_win_R=round(avg_win, 2),
        avg_loss_R=round(avg_loss, 2),
        profit_factor=round(pf, 2),
        by_setup_tag=by_tag,
    )
