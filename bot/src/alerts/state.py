"""Per-(symbol, side) alert state tracker + session-wide TOP PICK tracker.

A ticker can legitimately have a long alert and a short alert in the same
session (e.g. it gapped +30% on news, then the company priced an offering
30 minutes later — flips the bias). State is keyed by (symbol, side) so
both lanes are tracked independently.

Re-alert kinds:
  'new'           — first time we've seen this (symbol, side)
  'price_up'      — price moved >= +realert_price_pct since last alert
  'price_down'    — price moved <= -realert_price_pct since last alert
  'new_filing'    — fresh SEC filing on the ticker since last alert
  'vol_surge'     — premarket volume multiplied beyond threshold
  'orb_break_up'  — bullish opening range breakout
  'orb_break_down'— bearish opening range breakout
  'top_pick_new'  — this candidate just became the session leader
  None            — nothing notable, stay quiet

Session TOP PICK behavior:
  - Tracks the highest-conviction/score candidate observed since the
    scanner started.
  - Stays put unless a new candidate beats the current leader's score by
    SESSION_TOP_PICK_DELTA_PCT (default 5%) — prevents the gold marker
    from flicking back and forth between similar setups.
  - Emits 'top_pick_new' alert kind when the leader changes, with the
    previous leader's symbol included in the message.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Optional

import pytz

from ..config import CONFIG
from ..scanner.scanner import Candidate

NY = pytz.timezone("America/New_York")

AlertKind = str
Key = tuple[str, str]  # (symbol, side)


def _parse_hhmm(s: str) -> Optional[time]:
    try:
        h, m = s.split(":")
        return time(int(h), int(m))
    except (ValueError, AttributeError):
        return None


def in_trading_window() -> bool:
    """True when current NY time is inside [TRADING_WINDOW_START, TRADING_WINDOW_END]."""
    start = _parse_hhmm(getattr(CONFIG, "trading_window_start", "")) if CONFIG else None
    end = _parse_hhmm(getattr(CONFIG, "trading_window_end", "")) if CONFIG else None
    if start is None or end is None:
        return True  # window not configured = always alert
    now_ny = datetime.now(NY).time()
    if start <= end:
        return start <= now_ny <= end
    return now_ny >= start or now_ny <= end


@dataclass
class AlertRecord:
    symbol: str
    side: str
    initial_price: float
    last_alert_price: float
    last_alert_time: datetime
    filings_count: int
    last_pm_volume: int
    orb_high: Optional[float] = None
    orb_low: Optional[float] = None
    orb_break_alerted: bool = False


class AlertTracker:
    def __init__(
        self,
        price_threshold_pct: float | None = None,
        volume_multiple: float | None = None,
        cooldown_seconds: int | None = None,
    ):
        self.price_threshold_pct = price_threshold_pct or CONFIG.realert_price_pct
        self.volume_multiple = volume_multiple or CONFIG.realert_volume_multiple
        self.cooldown_seconds = cooldown_seconds or CONFIG.realert_cooldown_seconds
        self.records: dict[Key, AlertRecord] = {}
        # Session-wide top pick: (symbol, side, score, started_at)
        self.session_top: Optional[tuple[str, str, float, datetime]] = None
        self.session_top_history: list[tuple[str, str, float, datetime]] = []

    def update_session_top(self, candidates: list) -> tuple[Optional["object"], Optional[tuple[str, str, float]]]:
        """Decide the session leader and tag is_top_pick on candidates.

        Returns (new_leader_candidate or None, previous_leader tuple or None).
        Only returns a new leader when the top score beats the current
        leader's score by SESSION_TOP_PICK_DELTA_PCT.
        """
        # Reset all is_top_pick flags
        for c in candidates:
            c.is_top_pick = False
        if not candidates:
            return None, None

        best = max(candidates, key=lambda c: c.score)

        if self.session_top is None:
            self.session_top = (best.symbol, best.side, best.score, datetime.now(timezone.utc))
            best.is_top_pick = True
            return best, None

        prev_sym, prev_side, prev_score, _ = self.session_top
        delta_factor = 1.0 + (CONFIG.session_top_pick_delta_pct / 100.0)

        # Look for the previous leader in this scan to refresh its score
        current_leader = next(
            (c for c in candidates if c.symbol == prev_sym and c.side == prev_side),
            None,
        )

        if current_leader is None:
            # Previous leader fell out of qualifying candidates entirely → new leader
            self.session_top_history.append(self.session_top)
            self.session_top = (best.symbol, best.side, best.score, datetime.now(timezone.utc))
            best.is_top_pick = True
            return best, (prev_sym, prev_side, prev_score)

        # Decide: does best beat current_leader by enough?
        if best.symbol != current_leader.symbol and best.score >= current_leader.score * delta_factor:
            self.session_top_history.append(self.session_top)
            self.session_top = (best.symbol, best.side, best.score, datetime.now(timezone.utc))
            best.is_top_pick = True
            return best, (prev_sym, prev_side, prev_score)

        # Keep current leader; update its score in state
        self.session_top = (current_leader.symbol, current_leader.side, current_leader.score, self.session_top[3])
        current_leader.is_top_pick = True
        return None, None

    def _key(self, c: Candidate) -> Key:
        return (c.symbol, c.side)

    def classify(self, c: Candidate) -> Optional[AlertKind]:
        rec = self.records.get(self._key(c))
        if rec is None:
            return "new"

        # Lazy import to avoid circular dependency at module load
        from ..data.orb import compute_orb, detect_break

        # ORB break — only alert once per (symbol, side)
        if not rec.orb_break_alerted:
            if rec.orb_high is None or rec.orb_low is None:
                orb = compute_orb(c.symbol)
                if orb is not None:
                    rec.orb_high = orb.high
                    rec.orb_low = orb.low
            if rec.orb_high is not None:
                from ..data.orb import ORB
                fake = ORB(c.symbol, rec.orb_high, rec.orb_low or 0, 0, 0,
                           captured_at=datetime.now(timezone.utc))
                br = detect_break(c.symbol, fake)
                if (br == "orb_break_up" and c.side == "long") or \
                   (br == "orb_break_down" and c.side == "short"):
                    rec.orb_break_alerted = True
                    return br

        elapsed = (datetime.now(timezone.utc) - rec.last_alert_time).total_seconds()
        if elapsed < self.cooldown_seconds:
            return None

        if len(c.filings) > rec.filings_count:
            return "new_filing"

        pct_change = (c.quote.last - rec.last_alert_price) / rec.last_alert_price * 100
        if pct_change >= self.price_threshold_pct:
            return "price_up"
        if pct_change <= -self.price_threshold_pct:
            return "price_down"

        if rec.last_pm_volume > 0 and c.quote.premarket_volume >= rec.last_pm_volume * self.volume_multiple:
            return "vol_surge"

        return None

    def record(self, c: Candidate) -> None:
        key = self._key(c)
        existing = self.records.get(key)
        initial = existing.initial_price if existing else c.quote.last
        self.records[key] = AlertRecord(
            symbol=c.symbol,
            side=c.side,
            initial_price=initial,
            last_alert_price=c.quote.last,
            last_alert_time=datetime.now(timezone.utc),
            filings_count=len(c.filings),
            last_pm_volume=c.quote.premarket_volume,
        )

    def initial_price(self, c: Candidate) -> Optional[float]:
        rec = self.records.get(self._key(c))
        return rec.initial_price if rec else None
