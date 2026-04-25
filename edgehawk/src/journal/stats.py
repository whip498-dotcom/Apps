"""Per-setup statistics — the leverage point of the whole system.

After every 25-50 trades, run `python -m src.cli stats` and look at:

  - expectancy_R: average R won per trade. < 0 means stop trading that setup.
  - win_rate: directional bias. Combined with R, drives Kelly.
  - sample_size: <30 = too noisy to trust.

Sort the output. Size up the top quartile, cut the bottom.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .journal import Trade, all_trades, trade_pnl


@dataclass
class SetupStats:
    setup: str
    n: int
    win_rate: float
    avg_win_R: float
    avg_loss_R: float
    expectancy_R: float
    total_pnl: float
    avg_pct_return: float
    profit_factor: float


def compute_stats() -> list[SetupStats]:
    by_setup: dict[str, list[Trade]] = defaultdict(list)
    for t in all_trades():
        if t.exit_price is None:
            continue
        by_setup[t.setup].append(t)

    out: list[SetupStats] = []
    for setup, trades in by_setup.items():
        results = [trade_pnl(t) for t in trades]
        results = [r for r in results if r is not None]
        if not results:
            continue

        wins = [r for r in results if r.pnl > 0]
        losses = [r for r in results if r.pnl <= 0]
        win_rate = len(wins) / len(results)

        avg_win_R = sum(r.r_multiple for r in wins) / len(wins) if wins else 0.0
        avg_loss_R = sum(r.r_multiple for r in losses) / len(losses) if losses else 0.0
        expectancy_R = win_rate * avg_win_R + (1 - win_rate) * avg_loss_R
        total_pnl = sum(r.pnl for r in results)
        avg_pct = sum(r.pct_return for r in results) / len(results)

        gross_win = sum(r.pnl for r in wins)
        gross_loss = abs(sum(r.pnl for r in losses))
        pf = gross_win / gross_loss if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0

        out.append(SetupStats(
            setup=setup,
            n=len(results),
            win_rate=win_rate,
            avg_win_R=avg_win_R,
            avg_loss_R=avg_loss_R,
            expectancy_R=expectancy_R,
            total_pnl=total_pnl,
            avg_pct_return=avg_pct,
            profit_factor=pf,
        ))

    out.sort(key=lambda s: s.expectancy_R, reverse=True)
    return out


def overall_stats() -> dict:
    closed = [trade_pnl(t) for t in all_trades() if t.exit_price is not None]
    closed = [r for r in closed if r is not None]
    if not closed:
        return {"n": 0}
    wins = [r for r in closed if r.pnl > 0]
    losses = [r for r in closed if r.pnl <= 0]
    win_rate = len(wins) / len(closed)
    avg_win = sum(r.pnl for r in wins) / len(wins) if wins else 0.0
    avg_loss = sum(r.pnl for r in losses) / len(losses) if losses else 0.0
    return {
        "n": len(closed),
        "win_rate": win_rate,
        "avg_win_$": avg_win,
        "avg_loss_$": avg_loss,
        "total_pnl_$": sum(r.pnl for r in closed),
        "avg_R": sum(r.r_multiple for r in closed) / len(closed),
    }
