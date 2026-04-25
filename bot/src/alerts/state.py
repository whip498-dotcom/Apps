"""Per-symbol alert state tracker.

Decides whether each scan cycle's candidate is worth pinging Discord:

  - 'new'         — first time we've seen this symbol pass filters
  - 'price_up'    — price moved >= +realert_price_pct since last alert
  - 'price_down'  — price moved <= -realert_price_pct since last alert
  - 'new_filing'  — fresh SEC filing appeared since last alert
  - 'vol_surge'   — premarket volume multiplied beyond threshold
  - None          — no notable change, stay quiet

A per-symbol cooldown prevents alert spam if a ticker is whipping around.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from ..config import CONFIG
from ..scanner.scanner import Candidate

AlertKind = str  # 'new' | 'price_up' | 'price_down' | 'new_filing' | 'vol_surge'


@dataclass
class AlertRecord:
    symbol: str
    initial_price: float
    last_alert_price: float
    last_alert_time: datetime
    filings_count: int
    last_pm_volume: int


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
        self.records: dict[str, AlertRecord] = {}

    def classify(self, candidate: Candidate) -> Optional[AlertKind]:
        rec = self.records.get(candidate.symbol)
        if rec is None:
            return "new"

        elapsed = (datetime.now(timezone.utc) - rec.last_alert_time).total_seconds()
        if elapsed < self.cooldown_seconds:
            return None

        if len(candidate.filings) > rec.filings_count:
            return "new_filing"

        pct_change = (candidate.quote.last - rec.last_alert_price) / rec.last_alert_price * 100
        if pct_change >= self.price_threshold_pct:
            return "price_up"
        if pct_change <= -self.price_threshold_pct:
            return "price_down"

        if rec.last_pm_volume > 0 and candidate.quote.premarket_volume >= rec.last_pm_volume * self.volume_multiple:
            return "vol_surge"

        return None

    def record(self, candidate: Candidate) -> None:
        existing = self.records.get(candidate.symbol)
        initial = existing.initial_price if existing else candidate.quote.last
        self.records[candidate.symbol] = AlertRecord(
            symbol=candidate.symbol,
            initial_price=initial,
            last_alert_price=candidate.quote.last,
            last_alert_time=datetime.now(timezone.utc),
            filings_count=len(candidate.filings),
            last_pm_volume=candidate.quote.premarket_volume,
        )

    def initial_price(self, symbol: str) -> Optional[float]:
        rec = self.records.get(symbol)
        return rec.initial_price if rec else None
