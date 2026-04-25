"""Premarket scanner.

Pipeline:
  1. Build candidate universe (watchlist + EDGAR + market news)
  2. Pull premarket quotes
  3. Filter by price band, gap, premarket volume, relative volume
  4. Pull float per surviving symbol; drop floats > max
  5. Tag with catalyst news
  6. Score and rank
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..config import CONFIG
from ..data.edgar import Filing, fetch_recent_filings, filings_by_ticker
from ..data.float_data import get_float
from ..data.news import NewsItem, has_catalyst
from ..data.price import Quote, fetch_quotes
from ..data.universe import build_universe


@dataclass
class Candidate:
    quote: Quote
    float_shares: int | None
    catalysts: list[NewsItem] = field(default_factory=list)
    filings: list[Filing] = field(default_factory=list)
    score: float = 0.0
    flags: list[str] = field(default_factory=list)

    @property
    def symbol(self) -> str:
        return self.quote.symbol

    @property
    def has_dilution_risk(self) -> bool:
        return any(f.is_dilutive for f in self.filings) or any(n.is_dilutive for n in self.catalysts)


def _passes_price_band(q: Quote) -> bool:
    return CONFIG.price_min <= q.last <= CONFIG.price_max


def _passes_gap(q: Quote) -> bool:
    return q.gap_pct >= CONFIG.min_gap_pct


def _passes_volume(q: Quote) -> bool:
    return q.premarket_volume >= CONFIG.min_premarket_volume


def _passes_rvol(q: Quote) -> bool:
    return q.relative_volume >= CONFIG.min_relative_volume


def _passes_float(float_shares: int | None) -> bool:
    if float_shares is None:
        # Be conservative: skip when unknown to avoid trapped large-caps
        return False
    return float_shares <= CONFIG.max_float


def _score(c: Candidate) -> float:
    """Composite score — higher is more attractive.

    Weights are starting points, not gospel. Tune via the journal once you
    have 50+ tagged trades and know which factors actually pay you.
    """
    s = 0.0
    s += min(c.quote.gap_pct, 100.0) * 1.0          # bigger gap = bigger move
    s += min(c.quote.relative_volume, 50.0) * 2.0    # rvol is the king signal
    if c.float_shares:
        # Smaller float scores more, capped
        s += max(0.0, (30_000_000 - c.float_shares) / 1_000_000)
    s += 15.0 * sum(1 for n in c.catalysts if "FDA" in n.tags)
    s += 10.0 * sum(1 for n in c.catalysts if any(t in n.tags for t in ("PHASE3", "MERGER", "BUYOUT", "CONTRACT")))
    s += 5.0 * sum(1 for n in c.catalysts if n.tags)
    if c.has_dilution_risk:
        s -= 25.0
    return s


def scan() -> list[Candidate]:
    universe = build_universe()
    if not universe:
        return []

    quotes = fetch_quotes(universe)

    survivors: list[Quote] = [
        q for q in quotes.values()
        if _passes_price_band(q) and _passes_gap(q) and _passes_volume(q) and _passes_rvol(q)
    ]

    edgar_by_ticker = filings_by_ticker(fetch_recent_filings())

    candidates: list[Candidate] = []
    for q in survivors:
        fs = get_float(q.symbol)
        if not _passes_float(fs):
            continue
        _, news = has_catalyst(q.symbol, hours=24)
        c = Candidate(
            quote=q,
            float_shares=fs,
            catalysts=news,
            filings=edgar_by_ticker.get(q.symbol, []),
        )
        flags = []
        if c.has_dilution_risk:
            flags.append("DILUTION_RISK")
        if not c.catalysts and not c.filings:
            flags.append("NO_CATALYST")
        c.flags = flags
        c.score = _score(c)
        candidates.append(c)

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates


def scan_summary(c: Candidate) -> str:
    parts = [
        f"${c.quote.last:.2f}",
        f"gap +{c.quote.gap_pct:.1f}%",
        f"rvol {c.quote.relative_volume:.1f}x",
        f"pmVol {c.quote.premarket_volume:,}",
    ]
    if c.float_shares:
        parts.append(f"float {c.float_shares/1_000_000:.1f}M")
    if c.catalysts:
        top = c.catalysts[0]
        tag = ",".join(top.tags) if top.tags else "news"
        parts.append(f"[{tag}] {top.headline[:80]}")
    if c.flags:
        parts.append("⚠ " + ",".join(c.flags))
    return " | ".join(parts)
