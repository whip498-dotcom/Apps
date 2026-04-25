"""Build backtest Setup objects from the alert log.

Each HIGH-conviction alert that included a full trade plan (entry/stop/
target_1/target_2) becomes a Setup. The backtest engine replays it on
the historical 1m bars from that day to see whether the plan would have
won, lost, or never triggered.

This is the auto-backtest input — no manual CSV needed.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from ..config import CONFIG
from .engine import Setup

ALERT_LOG = CONFIG.cache_dir / "alert_log.jsonl"
CONVICTION_RANK = {"high": 3, "medium": 2, "low": 1}


def build_setups_from_alerts(days: int = 7, min_conviction: str = "high") -> list[Setup]:
    if not ALERT_LOG.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    needed_rank = CONVICTION_RANK.get(min_conviction, 3)

    seen: set[tuple[str, str, str]] = set()  # (symbol, side, date) — first alert of the day per ticker
    out: list[Setup] = []
    for line in ALERT_LOG.read_text().splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        try:
            ts = datetime.fromisoformat(rec.get("logged_at", ""))
        except ValueError:
            continue
        if ts < cutoff:
            continue
        if rec.get("kind") != "new":
            continue
        if CONVICTION_RANK.get(rec.get("conviction"), 0) < needed_rank:
            continue

        for k in ("entry_low", "stop", "target_1", "target_2"):
            if rec.get(k) is None:
                break
        else:
            symbol = rec["symbol"]
            side = rec["side"]
            d = ts.date()
            key = (symbol, side, d.isoformat())
            if key in seen:
                continue
            seen.add(key)
            entry = (rec["entry_low"] + rec.get("entry_high", rec["entry_low"])) / 2
            out.append(Setup(
                symbol=symbol,
                trade_date=d,
                side=side,
                entry=entry,
                stop=float(rec["stop"]),
                target_1=float(rec["target_1"]),
                target_2=float(rec["target_2"]),
                setup_tag=rec.get("setup", ""),
                catalyst=rec.get("catalyst_top_tag", "") or "",
            ))
    return out
