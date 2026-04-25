"""End-of-day review: what scanned, what triggered, what you traded.

Posts a summary to Discord at end of session (manual or scheduled). Acts
as a built-in trading journal review — what worked, what didn't, what
you missed.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import CONFIG
from .journal import Trade, _engine, trade_pnl

ALERT_LOG = CONFIG.cache_dir / "alert_log.jsonl"


def log_alert(payload: dict) -> None:
    """Append a one-line JSON record per alert sent. Idempotent."""
    payload = {**payload, "logged_at": datetime.now(timezone.utc).isoformat()}
    with ALERT_LOG.open("a") as f:
        f.write(json.dumps(payload) + "\n")


def _read_today_alerts() -> list[dict]:
    if not ALERT_LOG.exists():
        return []
    today = datetime.now(timezone.utc).date()
    out: list[dict] = []
    for line in ALERT_LOG.read_text().splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            ts = datetime.fromisoformat(rec.get("logged_at", "")).date()
        except ValueError:
            continue
        if ts == today:
            out.append(rec)
    return out


def _today_trades() -> list[Trade]:
    today = datetime.now(timezone.utc).date()
    with Session(_engine) as s:
        rows = list(s.scalars(select(Trade).order_by(Trade.entry_time.desc())))
    return [t for t in rows if t.entry_time and t.entry_time.date() == today]


def build_summary() -> dict[str, Any]:
    alerts = _read_today_alerts()
    trades = _today_trades()

    by_conv: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    by_side: dict[str, int] = {"long": 0, "short": 0}
    top_picks: list[dict] = []
    for a in alerts:
        c = a.get("conviction", "low")
        by_conv[c] = by_conv.get(c, 0) + 1
        s = a.get("side", "long")
        by_side[s] = by_side.get(s, 0) + 1
        if a.get("is_top_pick"):
            top_picks.append({
                "symbol": a.get("symbol"), "side": a.get("side"),
                "score": a.get("score"), "setup": a.get("setup"),
            })

    closed = [trade_pnl(t) for t in trades if t.exit_price is not None]
    closed = [r for r in closed if r is not None]
    wins = [r for r in closed if r.pnl > 0]
    losses = [r for r in closed if r.pnl <= 0]
    total_pnl = sum(r.pnl for r in closed)
    open_count = sum(1 for t in trades if t.exit_price is None)

    return {
        "date": datetime.now(timezone.utc).date().isoformat(),
        "alerts_total": len(alerts),
        "alerts_by_conviction": by_conv,
        "alerts_by_side": by_side,
        "top_picks": top_picks,
        "trades_taken": len(trades),
        "trades_closed": len(closed),
        "trades_open": open_count,
        "win_rate": (len(wins) / len(closed)) if closed else 0.0,
        "total_pnl": total_pnl,
        "wins": len(wins),
        "losses": len(losses),
        "best_trade": max((r.pnl for r in closed), default=0.0),
        "worst_trade": min((r.pnl for r in closed), default=0.0),
    }


def daily_pnl_today() -> float:
    closed = [trade_pnl(t) for t in _today_trades() if t.exit_price is not None]
    closed = [r for r in closed if r is not None]
    return sum(r.pnl for r in closed)


def consecutive_losses_today() -> int:
    """Count consecutive losses ending at the most recent trade."""
    trades = _today_trades()
    closed_results = [trade_pnl(t) for t in trades if t.exit_price is not None]
    closed_results = [r for r in closed_results if r is not None]
    closed_results.sort(key=lambda r: r.trade.exit_time or datetime.now(timezone.utc), reverse=True)
    streak = 0
    for r in closed_results:
        if r.pnl <= 0:
            streak += 1
        else:
            break
    return streak
