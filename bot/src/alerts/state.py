"""Per-(symbol, side) alert state tracker.

A ticker can legitimately have a long alert and a short alert in the same
session (e.g. it gapped +30% on news, then the company priced an offering
30 minutes later — flips the bias). State is keyed by (symbol, side) so
both lanes are tracked independently.

Re-alert kinds:
  'new'         — first time we've seen this (symbol, side)
  'price_up'    — price moved >= +realert_price_pct since last alert
  'price_down'  — price moved <= -realert_price_pct since last alert
  'new_filing'  — fresh SEC filing on the ticker since last alert
  'vol_surge'   — premarket volume multiplied beyond threshold
  None          — nothing notable, stay quiet
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from ..config import CONFIG
from ..scanner.scanner import Candidate

AlertKind = str
Key = tuple[str, str]  # (symbol, side)


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
