"""Position sizing.

Two layers, both applied:

  1. Risk-based: shares such that (entry - stop) * shares = max_risk_$
  2. Caps: never exceed max_position_size_pct of equity.

Optional Kelly: if a setup has stats in the journal, scale the risk
per trade by 0.25 * Kelly. Quarter-Kelly is the standard retail safety
margin — full Kelly is for people who don't mind 50% drawdowns.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..config import CONFIG
from ..journal.stats import compute_stats


@dataclass
class SizeRecommendation:
    shares: int
    risk_dollars: float
    position_dollars: float
    method: str
    reason: str


def kelly_fraction(win_rate: float, avg_win_R: float, avg_loss_R: float) -> float:
    """Kelly assuming wins/losses are sized in R.

    f* = W/A - L/B  where W=win_rate, L=1-W, A=avg_win_R, B=|avg_loss_R|
    """
    if avg_win_R <= 0 or avg_loss_R >= 0:
        return 0.0
    b = abs(avg_loss_R)
    a = avg_win_R
    return win_rate / b - (1 - win_rate) / a


def size_trade(
    entry: float,
    stop: float,
    setup: Optional[str] = None,
    equity: Optional[float] = None,
) -> SizeRecommendation:
    if entry <= 0 or stop <= 0 or entry == stop:
        raise ValueError("entry and stop must be positive and distinct")
    eq = equity if equity is not None else CONFIG.account_equity
    risk_per_share = abs(entry - stop)

    base_risk_pct = CONFIG.max_risk_per_trade_pct / 100.0
    risk_pct = base_risk_pct
    method = "fixed_risk"
    reason = f"{CONFIG.max_risk_per_trade_pct:.1f}% account risk"

    if setup:
        for s in compute_stats():
            if s.setup == setup and s.n >= 20 and s.expectancy_R > 0:
                k = kelly_fraction(s.win_rate, s.avg_win_R, s.avg_loss_R)
                quarter_k = max(0.0, k * 0.25)
                # Cap quarter-Kelly at 2x base — sanity bound
                risk_pct = min(quarter_k, base_risk_pct * 2)
                method = "quarter_kelly"
                reason = (
                    f"setup={setup} n={s.n} winRate={s.win_rate:.1%} "
                    f"E[R]={s.expectancy_R:.2f} K={k:.3f} → 0.25K={quarter_k:.3f}"
                )
                break

    risk_dollars = eq * risk_pct
    shares_by_risk = int(risk_dollars // risk_per_share)

    pos_cap_dollars = eq * (CONFIG.max_position_size_pct / 100.0)
    shares_by_cap = int(pos_cap_dollars // entry)

    shares = max(0, min(shares_by_risk, shares_by_cap))
    if shares == shares_by_cap and shares_by_cap < shares_by_risk:
        reason += f" | capped by max_position_size {CONFIG.max_position_size_pct:.0f}%"

    return SizeRecommendation(
        shares=shares,
        risk_dollars=shares * risk_per_share,
        position_dollars=shares * entry,
        method=method,
        reason=reason,
    )
