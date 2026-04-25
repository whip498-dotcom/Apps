"""Short-side candidate pool for the Daily Briefing.

The wider EdgeHawk scanner is intentionally long-only — its filters
(`gap >= +10%`) won't surface fade or breakdown setups. To give Claude
both directions of the day's tape, this module sweeps the same universe
with short-bias filters using the same data primitives (price, levels,
short interest) so we don't fork or shadow scanner.scan().

Two short shapes are included:
    - Overextended fade: gap >= +40%, rvol high, often paired with a
      recent dilution filing or "no catalyst" tag.
    - Gap-down momentum: gap <= -5% with rvol >= 2x — names already
      breaking down on volume.

Output is candidate-only data — Claude does the final 3-of-N pick in
the briefing prompt.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config import CONFIG
from ..data.float_data import get_float
from ..data.price import Quote, fetch_quotes
from ..data.short_interest import ShortInterest, get_short_interest
from ..data.universe import build_universe


_OVEREXTENDED_GAP_PCT = 40.0       # fade candidates above this gap
_BREAKDOWN_GAP_PCT = -5.0          # gap-down threshold
_BREAKDOWN_MIN_RVOL = 2.0


@dataclass
class ShortCandidate:
    quote: Quote
    float_shares: int | None
    short_interest: ShortInterest | None
    shape: str  # "overextended" | "breakdown"
    flags: list[str] = field(default_factory=list)

    @property
    def symbol(self) -> str:
        return self.quote.symbol


def _qualifies(q: Quote) -> tuple[bool, str]:
    if not (CONFIG.price_min <= q.last <= CONFIG.price_max):
        return False, ""
    if q.gap_pct >= _OVEREXTENDED_GAP_PCT and q.relative_volume >= 1.5:
        return True, "overextended"
    if q.gap_pct <= _BREAKDOWN_GAP_PCT and q.relative_volume >= _BREAKDOWN_MIN_RVOL:
        return True, "breakdown"
    return False, ""


def gather_shorts(max_results: int = 10) -> list[ShortCandidate]:
    universe = build_universe()
    if not universe:
        return []
    quotes = fetch_quotes(universe)

    out: list[ShortCandidate] = []
    for q in quotes.values():
        ok, shape = _qualifies(q)
        if not ok:
            continue
        fs = get_float(q.symbol)
        si = get_short_interest(q.symbol)
        flags: list[str] = []
        if shape == "overextended":
            flags.append("FADE_SETUP")
        if shape == "breakdown":
            flags.append("BREAKDOWN")
        if fs is not None and fs < 30_000_000:
            flags.append("LOW_FLOAT")
        if si and si.short_pct_float is not None and si.short_pct_float >= 0.20:
            # High SI fades are dangerous — flag them so Claude can avoid
            flags.append("HIGH_SI_RISK")
        out.append(ShortCandidate(
            quote=q,
            float_shares=fs,
            short_interest=si,
            shape=shape,
            flags=flags,
        ))

    # Rank by attractiveness: overextended w/ no high-SI risk first, then breakdowns
    def rank_key(c: ShortCandidate) -> tuple:
        risky = "HIGH_SI_RISK" in c.flags
        # Lower is better
        return (
            risky,
            c.shape != "overextended",
            -abs(c.quote.gap_pct),
            -c.quote.relative_volume,
        )
    out.sort(key=rank_key)
    return out[:max_results]
