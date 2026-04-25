"""Auto-scheduler: runs IBKR import, daily review, and weekly backtest
without you remembering. Hooks into the scanner loop and ticks once per
scan cycle. State persists to a JSON file so restarts don't double-run.

Tasks:
  - IBKR Flex import: every AUTO_IBKR_IMPORT_MINUTES (default 10) during
    9:30 ET → 16:30 ET, and once on first scan after market close.
    Skipped if IBKR_FLEX_TOKEN is not set.

  - Daily review: posts to Discord on the next scan after AUTO_DAILY_REVIEW_AFTER
    (default 16:30 NY) for any market day not yet posted. If you only run
    the scanner premarket, the review fires next morning at startup —
    catching up the previous day.

  - Auto-backtest: weekly on AUTO_BACKTEST_WEEKDAY (default Sunday) after
    AUTO_BACKTEST_AFTER. Builds setups from the last 7 days of HIGH-conviction
    alerts (alert_log.jsonl) and replays them via Polygon historical bars.
    Skipped if POLYGON_BACKTEST_KEY is not set.

All failures are caught and printed; a failed task does not block scanning.
"""
from __future__ import annotations

import json
import traceback
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable, Optional

import pytz

from .alerts.discord import send_daily_review, send_text
from .config import CONFIG

NY = pytz.timezone("America/New_York")
STATE_FILE: Path = CONFIG.cache_dir / "scheduler_state.json"


def _parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def _now_ny() -> datetime:
    return datetime.now(NY)


def _is_weekday(d: date) -> bool:
    return d.weekday() < 5


def _last_market_day(now: datetime) -> date:
    """Most recent weekday whose market has closed (or is closing right now)."""
    d = now.date()
    if _is_weekday(d) and now.time() >= time(16, 0):
        return d
    # Walk back to the previous weekday
    d -= timedelta(days=1)
    while not _is_weekday(d):
        d -= timedelta(days=1)
    return d


def _last_completed_week(now: datetime) -> str:
    """ISO week of the most recently completed week (Sun > Mon < Sun)."""
    # We back up to the previous Sunday
    days_since_sunday = (now.weekday() + 1) % 7
    last_sunday = now.date() - timedelta(days=days_since_sunday)
    iso_year, iso_week, _ = last_sunday.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


@dataclass
class TickReport:
    ran: list[str]
    skipped: list[str]
    errors: list[str]


class Scheduler:
    def __init__(self, log: Optional[Callable[[str], None]] = None):
        self.log = log or (lambda s: print(s))
        self.state = self._load_state()

    # ---------- state ----------
    def _load_state(self) -> dict:
        if not STATE_FILE.exists():
            return {}
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            return {}

    def _save_state(self) -> None:
        STATE_FILE.write_text(json.dumps(self.state, indent=2))

    # ---------- public entry ----------
    def tick(self, force: bool = False) -> TickReport:
        report = TickReport([], [], [])
        for name, fn in [
            ("ibkr_import",  self._maybe_ibkr_import),
            ("daily_review", self._maybe_daily_review),
            ("backtest",     self._maybe_backtest),
        ]:
            try:
                outcome = fn(force=force)
                if outcome == "ran":
                    report.ran.append(name)
                else:
                    report.skipped.append(name)
            except Exception as e:
                report.errors.append(f"{name}: {e}")
                self.log(f"[scheduler] {name} failed: {e}\n{traceback.format_exc()}")
        self._save_state()
        return report

    # ---------- IBKR ----------
    def _maybe_ibkr_import(self, force: bool = False) -> str:
        if not (CONFIG.ibkr_flex_token and CONFIG.ibkr_flex_query_id):
            return "skip:not_configured"

        now = _now_ny()
        last_iso = self.state.get("ibkr_last_iso")
        last = datetime.fromisoformat(last_iso).astimezone(NY) if last_iso else None
        in_window = time(9, 30) <= now.time() <= time(16, 30)

        # Run if:
        #   - never run
        #   - in window AND last run >= interval minutes ago
        #   - just past close AND not yet run today
        should_run = force or last is None
        if not should_run and in_window and last is not None:
            elapsed = (now - last).total_seconds() / 60
            if elapsed >= CONFIG.auto_ibkr_import_minutes:
                should_run = True
        if not should_run and now.time() >= time(16, 5) and (last is None or last.date() != now.date()):
            should_run = True

        if not should_run:
            return "skip:not_due"

        from .journal.ibkr_flex import import_today
        result = import_today()
        self.state["ibkr_last_iso"] = now.isoformat()
        self.log(f"[scheduler] IBKR auto-import: {result}")
        return "ran"

    # ---------- Daily review ----------
    def _maybe_daily_review(self, force: bool = False) -> str:
        now = _now_ny()
        target = _last_market_day(now)
        # Don't try to post for "today" until after the configured time
        review_after = _parse_hhmm(CONFIG.auto_daily_review_after_hhmm)
        if target == now.date() and now.time() < review_after:
            return "skip:too_early"

        posted = set(self.state.get("review_posted_dates", []))
        if not force and target.isoformat() in posted:
            return "skip:already_posted"

        from .journal.review import build_summary
        summary = build_summary()
        # Build summary uses "today" — if posting catch-up for a prior date,
        # the date label in the summary will say today. Override:
        summary["date"] = target.isoformat()
        ok = send_daily_review(summary)
        if not ok:
            self.log("[scheduler] Daily review post failed (Discord webhook?)")
            return "skip:post_failed"

        posted.add(target.isoformat())
        self.state["review_posted_dates"] = sorted(posted)[-30:]  # keep last 30
        self.log(f"[scheduler] Daily review posted for {target}")
        return "ran"

    # ---------- Backtest ----------
    def _maybe_backtest(self, force: bool = False) -> str:
        if not CONFIG.polygon_backtest_key:
            return "skip:no_polygon_key"

        now = _now_ny()
        if not force:
            if now.weekday() != CONFIG.auto_backtest_weekday:
                return "skip:not_backtest_day"
            after = _parse_hhmm(CONFIG.auto_backtest_after_hhmm)
            if now.time() < after:
                return "skip:too_early"

        week_key = _last_completed_week(now)
        if not force and self.state.get("backtest_last_week") == week_key:
            return "skip:already_run"

        from .backtest.engine import run_backtest, summarize
        from .backtest.from_alerts import build_setups_from_alerts
        from .backtest.storage import save_run

        setups = build_setups_from_alerts(days=7, min_conviction="high")
        if not setups:
            self.log(f"[scheduler] Backtest: no qualifying alerts in last 7 days")
            self.state["backtest_last_week"] = week_key
            return "skip:no_setups"

        self.log(f"[scheduler] Auto-backtest starting: {len(setups)} setups (≈{len(setups)*15}s)")
        capped = setups[:30]
        results = run_backtest(capped)
        stats = summarize(results)
        save_run(stats, results, label=f"auto-{week_key}", source="auto-weekly")
        msg = (
            f"📈 **Auto-backtest — week {week_key}**\n"
            f"Setups: {stats.n_setups} (triggered: {stats.n_triggered})\n"
            f"Win rate: {stats.win_rate:.1%}  ·  E[R]: {stats.expectancy_R:+.2f}  ·  PF: {stats.profit_factor:.2f}\n"
        )
        if stats.by_setup_tag:
            msg += "**By setup:**\n"
            for tag, b in sorted(stats.by_setup_tag.items(), key=lambda kv: -kv[1]["expectancy_R"])[:10]:
                msg += f"  • `{tag}` n={b['n']} win={b['win_rate']:.0%} E[R]={b['expectancy_R']:+.2f}\n"
        send_text(msg)
        self.state["backtest_last_week"] = week_key
        self.log(f"[scheduler] Auto-backtest posted")
        return "ran"
