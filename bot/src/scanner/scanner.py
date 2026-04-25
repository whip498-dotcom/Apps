"""Premarket scanner with separate LONG and SHORT lanes.

Pipeline:
  1. Build candidate universe (watchlist + EDGAR + PR wires + Finviz + Finnhub)
  2. Pull premarket quotes (price, gap, premarket vol, avg vol, rvol)
  3. Filter by price band, premarket volume, relative volume
  4. Filter by float < SCAN_MAX_FLOAT
  5. Tag with catalyst news (Finnhub + PR wires) — weighted bullish/bearish scores
  6. Cross-reference recent SEC filings (dilution flag for shorts / longs)
  7. Branch into two lanes:
       LONG  — gap up >= min_gap_pct AND bullish_score >= long_min_bullish_score
       SHORT — (gap up >= short_min_gap_pct AND (bearish_score>=N OR has dilution OR parabolic))
  8. Compute entry / SL / TP / S-R levels per side
  9. Score and rank within each lane
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..config import CONFIG
from ..data.edgar import Filing, fetch_recent_filings, filings_by_ticker
from ..data.float_data import get_float
from ..data.levels import Levels, compute_levels
from ..data.news import NewsItem, has_catalyst
from ..data.price import Quote, fetch_quotes
from ..data.universe import build_universe


@dataclass
class Candidate:
    side: str  # 'long' | 'short'
    quote: Quote
    float_shares: int | None
    catalysts: list[NewsItem] = field(default_factory=list)
    filings: list[Filing] = field(default_factory=list)
    levels: Optional[Levels] = None
    setup: str = ""           # 'gap_and_go' | 'news_runner' | 'parabolic_fade' | 'dilution_short' | etc
    score: float = 0.0
    flags: list[str] = field(default_factory=list)

    @property
    def symbol(self) -> str:
        return self.quote.symbol

    @property
    def has_dilution_risk(self) -> bool:
        return any(f.is_dilutive for f in self.filings) or any(n.is_dilutive for n in self.catalysts)

    @property
    def bullish_score(self) -> float:
        return sum(n.bullish_score for n in self.catalysts)

    @property
    def bearish_score(self) -> float:
        return sum(n.bearish_score for n in self.catalysts)


# ---------- shared filters ----------

def _passes_price_band(q: Quote) -> bool:
    return CONFIG.price_min <= q.last <= CONFIG.price_max


def _passes_volume(q: Quote) -> bool:
    return q.premarket_volume >= CONFIG.min_premarket_volume


def _passes_rvol(q: Quote) -> bool:
    return q.relative_volume >= CONFIG.min_relative_volume


def _passes_float(float_shares: int | None) -> bool:
    if float_shares is None:
        return False
    return float_shares <= CONFIG.max_float


# ---------- long-lane logic ----------

def _qualifies_long(q: Quote, news: list[NewsItem], filings: list[Filing]) -> tuple[bool, str]:
    if q.gap_pct < CONFIG.min_gap_pct:
        return False, ""
    bullish = sum(n.bullish_score for n in news)
    bearish = sum(n.bearish_score for n in news)
    has_dilution = any(f.is_dilutive for f in filings) or any(n.is_dilutive for n in news)

    if has_dilution:
        return False, ""               # never go long into fresh dilution
    if bearish > bullish:
        return False, ""               # net-bearish news, skip

    if bullish >= CONFIG.long_min_bullish_score:
        # Setup name based on top tag
        if news and news[0].matches:
            top = max(news[0].matches, key=lambda m: abs(m[1]))[0]
            return True, f"news_{top.lower()}"
        return True, "news_runner"

    # No qualifying news but big gap + rvol = pure technical breakout
    if q.relative_volume >= 5.0 and q.gap_pct >= 20:
        return True, "gap_and_go"

    return False, ""


# ---------- short-lane logic ----------

def _qualifies_short(q: Quote, news: list[NewsItem], filings: list[Filing]) -> tuple[bool, str]:
    bearish = sum(n.bearish_score for n in news)
    has_dilution = any(f.is_dilutive for f in filings) or any(n.is_dilutive for n in news)

    # Path 1: dilution short — fresh offering filing into a gapper
    if has_dilution and q.gap_pct >= 15:
        return True, "dilution_short"

    # Path 2: bearish-news fade — earnings miss / FDA reject / clinical hold
    if bearish >= CONFIG.short_min_bearish_score and q.gap_pct >= CONFIG.short_min_gap_pct - 10:
        return True, "news_fade"

    # Path 3: parabolic extension — huge gap with no news context = fade candidate
    if q.gap_pct >= CONFIG.short_parabolic_extension_pct:
        return True, "parabolic_fade"

    return False, ""


# ---------- scoring ----------

def _score_long(c: Candidate) -> float:
    s = 0.0
    s += min(c.quote.gap_pct, 100.0) * 1.0
    s += min(c.quote.relative_volume, 50.0) * 2.0
    if c.float_shares:
        s += max(0.0, (CONFIG.max_float - c.float_shares) / 1_000_000)
    s += c.bullish_score
    s -= c.bearish_score
    if c.has_dilution_risk:
        s -= 30
    if c.levels and c.levels.rr_target_1 >= 2.0:
        s += 8
    return s


def _score_short(c: Candidate) -> float:
    s = 0.0
    s += min(c.quote.gap_pct, 200.0) * 0.6     # bigger gap = more rope to fade
    s += min(c.quote.relative_volume, 50.0) * 1.5
    if c.float_shares:
        s += max(0.0, (CONFIG.max_float - c.float_shares) / 1_000_000)
    s += c.bearish_score
    s += 20 if c.has_dilution_risk else 0
    if c.levels and c.levels.rr_target_1 >= 2.0:
        s += 8
    return s


# ---------- main ----------

def scan() -> list[Candidate]:
    universe = build_universe()
    if not universe:
        return []

    quotes = fetch_quotes(universe)

    survivors: list[Quote] = [
        q for q in quotes.values()
        if _passes_price_band(q) and _passes_volume(q) and _passes_rvol(q)
    ]

    edgar_by_ticker = filings_by_ticker(fetch_recent_filings())
    candidates: list[Candidate] = []

    for q in survivors:
        fs = get_float(q.symbol)
        if not _passes_float(fs):
            continue

        _, news = has_catalyst(q.symbol, hours=24)
        filings = edgar_by_ticker.get(q.symbol, [])

        # LONG lane
        if CONFIG.enable_long_lane:
            ok, setup = _qualifies_long(q, news, filings)
            if ok:
                lv = compute_levels(q.symbol, "long", q.last)
                c = Candidate(
                    side="long", quote=q, float_shares=fs,
                    catalysts=news, filings=filings, levels=lv, setup=setup,
                )
                if c.has_dilution_risk:
                    c.flags.append("DILUTION_RISK")
                if not c.catalysts:
                    c.flags.append("NO_CATALYST")
                if lv is None:
                    c.flags.append("NO_LEVELS")
                c.score = _score_long(c)
                candidates.append(c)

        # SHORT lane
        if CONFIG.enable_short_lane:
            ok, setup = _qualifies_short(q, news, filings)
            if ok:
                lv = compute_levels(q.symbol, "short", q.last)
                c = Candidate(
                    side="short", quote=q, float_shares=fs,
                    catalysts=news, filings=filings, levels=lv, setup=setup,
                )
                if c.has_dilution_risk:
                    c.flags.append("DILUTION")
                if lv is None:
                    c.flags.append("NO_LEVELS")
                c.score = _score_short(c)
                candidates.append(c)

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates


def scan_summary(c: Candidate) -> str:
    parts = [
        f"{c.side.upper()}",
        f"${c.quote.last:.2f}",
        f"gap +{c.quote.gap_pct:.1f}%",
        f"rvol {c.quote.relative_volume:.1f}x",
    ]
    if c.float_shares:
        parts.append(f"float {c.float_shares/1_000_000:.1f}M")
    if c.setup:
        parts.append(f"setup={c.setup}")
    if c.flags:
        parts.append("⚠ " + ",".join(c.flags))
    return " | ".join(parts)
