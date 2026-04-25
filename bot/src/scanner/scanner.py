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
class OvernightMover:
    """A top mover from the universe based on absolute gap from prior close.

    Distinct from Candidate — these are *informational*. They may not pass
    the trade-lane filters (price band, float, qualifying catalyst), but
    they're useful context: 'here's what woke up overnight in your universe.'
    """
    quote: Quote
    levels: Optional[Levels]
    catalysts: list[NewsItem] = field(default_factory=list)
    filings: list[Filing] = field(default_factory=list)
    short_interest_pct: Optional[float] = None
    days_to_cover: Optional[float] = None

    @property
    def symbol(self) -> str:
        return self.quote.symbol

    @property
    def gap_pct(self) -> float:
        return self.quote.gap_pct

    @property
    def direction(self) -> str:
        return "up" if self.quote.gap_pct >= 0 else "down"

    @property
    def has_dilution_risk(self) -> bool:
        return any(f.is_dilutive for f in self.filings) or any(n.is_dilutive for n in self.catalysts)

    @property
    def top_catalyst(self) -> Optional[NewsItem]:
        return self.catalysts[0] if self.catalysts else None


@dataclass
class ScanResult:
    """One scan cycle output: trade-qualified candidates + universe movers."""
    candidates: list["Candidate"] = field(default_factory=list)
    movers: list[OvernightMover] = field(default_factory=list)


@dataclass
class Candidate:
    side: str  # 'long' | 'short'
    quote: Quote
    float_shares: int | None
    catalysts: list[NewsItem] = field(default_factory=list)
    filings: list[Filing] = field(default_factory=list)
    levels: Optional[Levels] = None
    setup: str = ""
    score: float = 0.0
    conviction: str = "low"   # 'high' | 'medium' | 'low'
    conviction_reasons: list[str] = field(default_factory=list)
    short_interest_pct: Optional[float] = None
    days_to_cover: Optional[float] = None
    flags: list[str] = field(default_factory=list)
    is_top_pick: bool = False

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

    @property
    def float_rotation(self) -> float:
        return self.quote.float_rotation


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
    # Float rotation bonus — premarket volume actually consuming the float
    rot = c.float_rotation
    if rot >= CONFIG.rotation_parabolic_threshold:
        s += 25
    elif rot >= CONFIG.rotation_warn_threshold:
        s += 12
    return s


def _score_short(c: Candidate) -> float:
    s = 0.0
    s += min(c.quote.gap_pct, 200.0) * 0.6
    s += min(c.quote.relative_volume, 50.0) * 1.5
    if c.float_shares:
        s += max(0.0, (CONFIG.max_float - c.float_shares) / 1_000_000)
    s += c.bearish_score
    s += 20 if c.has_dilution_risk else 0
    if c.levels and c.levels.rr_target_1 >= 2.0:
        s += 8
    # Parabolic rotation favors fades (exhaustion)
    rot = c.float_rotation
    if rot >= CONFIG.rotation_parabolic_threshold:
        s += 18
    return s


def _classify_conviction(c: Candidate) -> tuple[str, list[str]]:
    """HIGH = score above threshold + at least one strong confirming signal.

    Returns (tier, reasons). Reasons explain *why* the call is high conviction
    so the user can verify rather than blindly trust the bot.
    """
    reasons: list[str] = []

    # Strong signals — count how many are present
    strong = 0

    if c.bullish_score >= 25 and c.side == "long":
        reasons.append(f"Strong bullish news ({c.bullish_score:.0f})")
        strong += 1
    if c.bearish_score >= 25 and c.side == "short":
        reasons.append(f"Strong bearish news ({c.bearish_score:.0f})")
        strong += 1

    if c.has_dilution_risk and c.side == "short":
        reasons.append("Fresh dilution filing")
        strong += 1

    if c.float_rotation >= CONFIG.rotation_parabolic_threshold:
        reasons.append(f"Float rotated {c.float_rotation:.1f}x (parabolic)")
        strong += 1
    elif c.float_rotation >= CONFIG.rotation_warn_threshold:
        reasons.append(f"Float rotated {c.float_rotation:.1f}x")

    if c.quote.relative_volume >= 10:
        reasons.append(f"Extreme rvol {c.quote.relative_volume:.1f}x")
        strong += 1

    if c.levels and c.levels.rr_target_1 >= 2.5:
        reasons.append(f"R:R {c.levels.rr_target_1:.1f} to TP1")

    if c.float_shares and c.float_shares <= 10_000_000:
        reasons.append(f"Tiny float {c.float_shares/1_000_000:.1f}M")

    if c.score >= CONFIG.high_conviction_min_score and strong >= 2:
        return "high", reasons
    if c.score >= CONFIG.medium_conviction_min_score and (strong >= 1 or len(reasons) >= 2):
        return "medium", reasons
    return "low", reasons


# ---------- main ----------

def _compute_movers(
    quotes: dict,
    edgar_by_ticker: dict,
    top_n: int = 5,
    min_abs_gap_pct: float = 5.0,
    min_volume: int = 10_000,
    price_min: float = 1.0,
    price_max: float = 50.0,
) -> list[OvernightMover]:
    """Top N absolute movers from the universe — broader than trade-lane filters.

    Used for the morning brief and the live tile 'Overnight movers' section.
    """
    eligible = [
        q for q in quotes.values()
        if price_min <= q.last <= price_max
        and abs(q.gap_pct) >= min_abs_gap_pct
        and q.premarket_volume >= min_volume
    ]
    eligible.sort(key=lambda q: abs(q.gap_pct), reverse=True)

    out: list[OvernightMover] = []
    for q in eligible[:top_n]:
        try:
            fs = get_float(q.symbol)
            q.float_shares = fs
        except Exception:
            fs = None
        try:
            _, news = has_catalyst(q.symbol, hours=24)
        except Exception:
            news = []
        filings = edgar_by_ticker.get(q.symbol, [])
        # Always compute long-side levels for context (PDH/PDL/VWAP/pivots
        # are direction-agnostic; the entry/stop/TP zones are placeholder).
        try:
            lv = compute_levels(q.symbol, "long", q.last)
        except Exception:
            lv = None
        out.append(OvernightMover(
            quote=q, levels=lv, catalysts=news, filings=filings,
        ))
    return out


def scan() -> ScanResult:
    universe = build_universe()
    if not universe:
        return ScanResult()

    quotes = fetch_quotes(universe)
    edgar_by_ticker = filings_by_ticker(fetch_recent_filings())

    # Top universe movers — informational context, not gated by trade-lane filters
    movers = _compute_movers(quotes, edgar_by_ticker, top_n=5)

    candidates: list[Candidate] = []

    survivors: list[Quote] = [
        q for q in quotes.values()
        if _passes_price_band(q) and _passes_volume(q) and _passes_rvol(q)
    ]

    for q in survivors:
        fs = get_float(q.symbol)
        if not _passes_float(fs):
            continue
        q.float_shares = fs  # so float_rotation works

        _, news = has_catalyst(q.symbol, hours=24)
        filings = edgar_by_ticker.get(q.symbol, [])

        # Optional short interest enrichment (lazy import — independent module)
        si_pct = dtc = None
        if CONFIG.enable_short_interest:
            try:
                from ..data.short_interest import get_short_interest
                si = get_short_interest(q.symbol)
                if si:
                    si_pct = si.short_interest_pct
                    dtc = si.days_to_cover
            except Exception:
                pass

        # LONG lane
        if CONFIG.enable_long_lane:
            ok, setup = _qualifies_long(q, news, filings)
            if ok:
                lv = compute_levels(q.symbol, "long", q.last)
                c = Candidate(
                    side="long", quote=q, float_shares=fs,
                    catalysts=news, filings=filings, levels=lv, setup=setup,
                    short_interest_pct=si_pct, days_to_cover=dtc,
                )
                if c.has_dilution_risk:
                    c.flags.append("DILUTION_RISK")
                if not c.catalysts:
                    c.flags.append("NO_CATALYST")
                if lv is None:
                    c.flags.append("NO_LEVELS")
                if q.float_rotation >= CONFIG.rotation_parabolic_threshold:
                    c.flags.append("FLOAT_ROTATED_PARABOLIC")
                elif q.float_rotation >= CONFIG.rotation_warn_threshold:
                    c.flags.append("FLOAT_ROTATED_1X+")
                c.score = _score_long(c)
                c.conviction, c.conviction_reasons = _classify_conviction(c)
                candidates.append(c)

        # SHORT lane
        if CONFIG.enable_short_lane:
            ok, setup = _qualifies_short(q, news, filings)
            if ok:
                lv = compute_levels(q.symbol, "short", q.last)
                c = Candidate(
                    side="short", quote=q, float_shares=fs,
                    catalysts=news, filings=filings, levels=lv, setup=setup,
                    short_interest_pct=si_pct, days_to_cover=dtc,
                )
                if c.has_dilution_risk:
                    c.flags.append("DILUTION")
                if lv is None:
                    c.flags.append("NO_LEVELS")
                if q.float_rotation >= CONFIG.rotation_parabolic_threshold:
                    c.flags.append("EXHAUSTION_RISK")
                # Squeeze risk for shorts
                if si_pct and si_pct >= 20:
                    c.flags.append(f"HIGH_SI_{si_pct:.0f}%")
                c.score = _score_short(c)
                c.conviction, c.conviction_reasons = _classify_conviction(c)
                candidates.append(c)

    candidates.sort(key=lambda c: c.score, reverse=True)
    if candidates:
        candidates[0].is_top_pick = True
    return ScanResult(candidates=candidates, movers=movers)


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
