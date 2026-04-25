"""Backtest result persistence.

Each completed backtest run appends one record to backtest_history.jsonl
in data_cache/. The dashboard reads the latest entry for its summary
card; CLI commands read history for older runs.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from ..config import CONFIG

HISTORY_FILE = CONFIG.cache_dir / "backtest_history.jsonl"


def save_run(stats, results, label: str = "manual", source: str = "cli") -> dict:
    """Persist a backtest run summary + per-trade results. Returns the record."""
    triggered = [r for r in results if r.triggered]
    record = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "label": label,
        "source": source,                  # 'cli' | 'auto-weekly'
        "n_setups": stats.n_setups,
        "n_triggered": stats.n_triggered,
        "win_rate": stats.win_rate,
        "expectancy_R": stats.expectancy_R,
        "avg_win_R": stats.avg_win_R,
        "avg_loss_R": stats.avg_loss_R,
        "profit_factor": stats.profit_factor,
        "by_setup_tag": stats.by_setup_tag,
        "trades": [
            {
                "symbol": r.setup.symbol,
                "trade_date": r.setup.trade_date.isoformat(),
                "side": r.setup.side,
                "setup_tag": r.setup.setup_tag,
                "catalyst": r.setup.catalyst,
                "entry": r.setup.entry,
                "stop": r.setup.stop,
                "target_1": r.setup.target_1,
                "target_2": r.setup.target_2,
                "triggered": r.triggered,
                "hit_target_1": r.hit_target_1,
                "hit_target_2": r.hit_target_2,
                "hit_stop": r.hit_stop,
                "exit_price": r.exit_price,
                "minutes_to_exit": r.minutes_to_exit,
                "r_multiple": r.r_multiple,
                "max_favorable_R": r.max_favorable_R,
                "max_adverse_R": r.max_adverse_R,
            }
            for r in triggered
        ],
    }
    with HISTORY_FILE.open("a") as f:
        f.write(json.dumps(record) + "\n")
    return record


def load_history(limit: int = 10) -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    lines = HISTORY_FILE.read_text().splitlines()[-limit:]
    out: list[dict] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def latest_run() -> Optional[dict]:
    history = load_history(limit=1)
    return history[0] if history else None
