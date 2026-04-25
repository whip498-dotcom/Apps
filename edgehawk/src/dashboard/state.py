"""Dashboard state file. Decouples the scanner from the dashboard server.

Scanner writes `dashboard_state.json` after each cycle.
Dashboard server reads it via the /api/state endpoint.

Filesystem-as-IPC: simplest possible loose coupling.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from ..config import CONFIG

STATE_FILE = CONFIG.cache_dir / "dashboard_state.json"


def _levels_dict(lv) -> Optional[dict]:
    if lv is None:
        return None
    return {
        "side": lv.side,
        "entry_low": lv.entry_low,
        "entry_high": lv.entry_high,
        "stop": lv.stop,
        "target_1": lv.target_1,
        "target_2": lv.target_2,
        "rr_target_1": lv.rr_target_1,
        "rr_target_2": lv.rr_target_2,
        "premarket_high": lv.premarket_high,
        "premarket_low": lv.premarket_low,
        "prior_day_high": lv.prior_day_high,
        "prior_day_low": lv.prior_day_low,
        "prior_day_close": lv.prior_day_close,
        "vwap": lv.vwap,
        "pivot": lv.pivot,
        "r1": lv.r1, "r2": lv.r2,
        "s1": lv.s1, "s2": lv.s2,
    }


def _catalyst_dict(n) -> Optional[dict]:
    if n is None:
        return None
    return {
        "headline": n.headline,
        "url": n.url,
        "source": n.source,
        "tags": n.tags,
        "published_at": n.published_at.isoformat() if n.published_at else None,
    }


def _filing_dict(f) -> dict:
    return {
        "form": f.form,
        "title": f.title,
        "link": f.link,
        "is_dilutive": f.is_dilutive,
        "filed_at": f.filed_at.isoformat() if f.filed_at else None,
    }


def _candidate_dict(c) -> dict:
    return {
        "symbol": c.symbol,
        "side": c.side,
        "setup": c.setup,
        "score": round(c.score, 1),
        "conviction": c.conviction,
        "conviction_reasons": c.conviction_reasons,
        "is_top_pick": c.is_top_pick,
        "price": round(c.quote.last, 2),
        "prev_close": round(c.quote.prev_close, 2),
        "gap_pct": round(c.quote.gap_pct, 2),
        "rvol": round(c.quote.relative_volume, 2),
        "premarket_volume": c.quote.premarket_volume,
        "float_shares": c.float_shares,
        "rotation": round(c.float_rotation, 2),
        "flags": c.flags,
        "has_dilution_risk": c.has_dilution_risk,
        "short_interest_pct": c.short_interest_pct,
        "days_to_cover": c.days_to_cover,
        "levels": _levels_dict(c.levels),
        "catalyst": _catalyst_dict(c.catalysts[0]) if c.catalysts else None,
        "filing": _filing_dict(c.filings[0]) if c.filings else None,
    }


def _mover_dict(m) -> dict:
    return {
        "symbol": m.symbol,
        "direction": m.direction,
        "gap_pct": round(m.gap_pct, 2),
        "price": round(m.quote.last, 2),
        "prev_close": round(m.quote.prev_close, 2),
        "premarket_volume": m.quote.premarket_volume,
        "rvol": round(m.quote.relative_volume, 2),
        "float_shares": m.quote.float_shares,
        "has_dilution_risk": m.has_dilution_risk,
        "levels": _levels_dict(m.levels),
        "catalyst": _catalyst_dict(m.top_catalyst),
        "filing": _filing_dict(m.filings[0]) if m.filings else None,
    }


def write_state(
    candidates,
    movers,
    window_status: str,
    *,
    new_top_pick_change: Optional[dict] = None,
) -> None:
    # Latest backtest summary (read-only — never writes)
    latest_bt = None
    try:
        from ..backtest.storage import latest_run
        latest_bt = latest_run()
    except Exception:
        latest_bt = None

    payload: dict[str, Any] = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "window_status": window_status,
        "trading_window": f"{CONFIG.trading_window_start}–{CONFIG.trading_window_end} NY",
        "discord_min_conviction": CONFIG.discord_min_conviction,
        "candidates": [_candidate_dict(c) for c in candidates],
        "movers": [_mover_dict(m) for m in movers],
        "new_top_pick_change": new_top_pick_change,
        "latest_backtest": latest_bt,
    }
    STATE_FILE.write_text(json.dumps(payload, indent=2))


def read_state() -> dict:
    if not STATE_FILE.exists():
        return {
            "updated_at": None,
            "window_status": "unknown",
            "trading_window": "",
            "candidates": [],
            "movers": [],
        }
    try:
        return json.loads(STATE_FILE.read_text())
    except json.JSONDecodeError:
        return {"updated_at": None, "candidates": [], "movers": []}
