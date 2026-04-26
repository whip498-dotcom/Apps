"""Squeeze-potential scanner.

Modelled on Bullish Bob's "Squeeze Potential / Key Levels" workflow:

  1. Build candidate universe (watchlist + EDGAR + market news)
  2. Pull premarket quotes + key levels (PMH/PML/PDH/PDL/ORH)
  3. Filter by price band, gap, premarket volume, relative volume
  4. Pull float per surviving symbol; drop floats >= max
  5. Pull short interest + days-to-cover (cached 24h)
  6. Tag with catalyst news / EDGAR filings
  7. Score the squeeze, distil to a 1-10 confidence, and rank
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..config import CONFIG
from ..data.edgar import Filing, fetch_recent_filings, filings_by_ticker
from ..data.float_data import get_float
from ..data.news import NewsItem, has_catalyst
from ..data.price import Quote, fetch_quotes
from ..data.short_interest import ShortInterest, get_short_interest
from ..data.universe import build_universe


@dataclass
class Candidate:
    quote: Quote
    float_shares: int | None
    short_interest: ShortInterest | None = None
    catalysts: list[NewsItem] = field(default_factory=list)
    filings: list[Filing] = field(default_factory=list)
    score: float = 0.0
    confidence: int = 0  # 1-10, capped
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
    return float_shares < CONFIG.max_float


def _passes_short_interest(si: ShortInterest | None) -> bool:
    if CONFIG.min_short_interest_pct <= 0:
        return True  # gate disabled
    if si is None or si.short_pct_float is None:
        return True  # don't punish missing data — let the score reflect uncertainty
    return si.short_pct_float * 100.0 >= CONFIG.min_short_interest_pct


# --- Scoring -----------------------------------------------------------------
#
# The raw _score is unbounded; _confidence squashes it into 1-10 using
# anchor points calibrated to a "perfect" small-cap squeeze setup:
#   gap +30%, RVOL 10x, float 5M, SI 35%, FDA-tier catalyst, near PMH
# That setup scores ~120 points; we map 0->1 and 120->10.

_PERFECT_SCORE = 120.0


def _score(c: Candidate) -> float:
    s = 0.0

    # --- Momentum (the move that's already happening) ---
    s += min(c.quote.gap_pct, 100.0) * 0.6           # cap influence at huge gaps
    s += min(c.quote.relative_volume, 50.0) * 1.5    # rvol is the king signal

    # --- Squeeze fuel (low float + short interest = combustible) ---
    if c.float_shares:
        # 30M -> 0 pts, 5M -> 25 pts (linear)
        s += max(0.0, (30_000_000 - c.float_shares) / 1_000_000)
    if c.short_interest and c.short_interest.short_pct_float is not None:
        si_pct = c.short_interest.short_pct_float * 100.0
        # 0-30% earns 1 pt per %; 30-50% earns 0.5 pt per %; >50% capped.
        base = min(si_pct, 30.0)
        bonus = max(0.0, min(si_pct, 50.0) - 30.0) * 0.5
        s += base + bonus  # max 40 pts at 50%+ SI
    if c.short_interest and c.short_interest.days_to_cover is not None:
        s += min(c.short_interest.days_to_cover, 10.0) * 1.5  # max 15 pts

    # --- Catalyst (the reason it's moving) ---
    s += 18.0 * sum(1 for n in c.catalysts if "FDA" in n.tags)
    s += 12.0 * sum(1 for n in c.catalysts if any(
        t in n.tags for t in ("PHASE3", "MERGER", "BUYOUT", "CONTRACT")))
    s += 5.0 * sum(1 for n in c.catalysts if n.tags)

    # --- Key level proximity (the trigger trader actually pulls) ---
    lv = c.quote.levels
    last = c.quote.last
    if lv.above_pmh(last):
        s += 10.0  # broken out of premarket — strongest signal
    elif lv.near_pmh(last, tolerance_pct=1.0):
        s += 6.0
    if lv.above_pdh(last):
        s += 6.0   # also above prior day high

    # --- Penalties ---
    if c.has_dilution_risk:
        s -= 25.0

    return s


def _confidence(score: float) -> int:
    if score <= 0:
        return 1
    raw = round(1 + (score / _PERFECT_SCORE) * 9)
    return max(1, min(10, int(raw)))


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
        si = get_short_interest(q.symbol)
        if not _passes_short_interest(si):
            continue
        _, news = has_catalyst(q.symbol, hours=24)
        c = Candidate(
            quote=q,
            float_shares=fs,
            short_interest=si,
            catalysts=news,
            filings=edgar_by_ticker.get(q.symbol, []),
        )
        flags = []
        if c.has_dilution_risk:
            flags.append("DILUTION_RISK")
        if not c.catalysts and not c.filings:
            flags.append("NO_CATALYST")
        if si and si.is_squeeze_candidate:
            flags.append("SQUEEZE")
        if q.levels.above_pmh(q.last):
            flags.append("PMH_BREAK")
        elif q.levels.near_pmh(q.last):
            flags.append("NEAR_PMH")
        c.flags = flags
        c.score = _score(c)
        c.confidence = _confidence(c.score)
        candidates.append(c)

    # Sort by confidence desc, score desc, then rvol/gap to break ties so
    # alphabetically-early symbols don't dominate when scores match.
    candidates.sort(
        key=lambda c: (c.confidence, c.score, c.quote.relative_volume, c.quote.gap_pct),
        reverse=True,
    )
    return candidates


def alert_worthy(c: Candidate) -> bool:
    """Whether a candidate clears the alert confidence gate (default >=8)."""
    return c.confidence >= CONFIG.min_confidence


def scan_summary(c: Candidate) -> str:
    parts = [
        f"conf {c.confidence}/10",
        f"${c.quote.last:.2f}",
        f"gap +{c.quote.gap_pct:.1f}%",
        f"rvol {c.quote.relative_volume:.1f}x",
        f"pmVol {c.quote.premarket_volume:,}",
    ]
    if c.float_shares:
        parts.append(f"float {c.float_shares/1_000_000:.1f}M")
    if c.short_interest and c.short_interest.short_pct_float is not None:
        parts.append(f"SI {c.short_interest.short_pct_float*100:.1f}%")
    if c.short_interest and c.short_interest.days_to_cover is not None:
        parts.append(f"DTC {c.short_interest.days_to_cover:.1f}d")
    lv = c.quote.levels
    if lv.pmh:
        parts.append(f"PMH ${lv.pmh:.2f}")
    if lv.pdh:
        parts.append(f"PDH ${lv.pdh:.2f}")
    if c.catalysts:
        top = c.catalysts[0]
        tag = ",".join(top.tags) if top.tags else "news"
        parts.append(f"[{tag}] {top.headline[:80]}")
    if c.flags:
        parts.append("⚠ " + ",".join(c.flags))
    return " | ".join(parts)
