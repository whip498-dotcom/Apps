"""Catalyst classification for news items (Finnhub + PR wires).

Weighted regex rules. Each rule emits:
  - tag (string label, e.g. FDA_APPROVAL)
  - weight (signed magnitude — positive = bullish, negative = bearish)
  - side ('long' | 'short' | 'neutral')

A NewsItem ends up with a list of matched (tag, weight, side) plus an
aggregate `bullish_score` and `bearish_score`. The scanner uses these to
decide which side a candidate qualifies for.

Adding a rule: append to CATALYST_RULES below. Keep regexes case-insensitive.
Test on real headlines before merging — false positives are expensive.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import finnhub

from ..config import CONFIG


@dataclass
class CatalystRule:
    pattern: re.Pattern
    tag: str
    weight: float          # > 0 bullish, < 0 bearish
    side: str              # 'long' | 'short' | 'neutral'


def _r(pat: str, tag: str, weight: float, side: str) -> CatalystRule:
    return CatalystRule(re.compile(pat, re.IGNORECASE), tag, weight, side)


# Order does not matter; all matching rules are applied.
CATALYST_RULES: list[CatalystRule] = [
    # --- Strong bullish ---
    _r(r"\bfda\s+approv(es|al|ed)\b",                      "FDA_APPROVAL",    35, "long"),
    _r(r"\bbreakthrough\s+(therapy\s+)?designation\b",     "FDA_BREAKTHROUGH", 25, "long"),
    _r(r"\bphase\s*3.*(positive|topline|met\b|success)",   "PHASE3_POSITIVE", 28, "long"),
    _r(r"\bphase\s*2.*(positive|topline|met\b|success)",   "PHASE2_POSITIVE", 18, "long"),
    _r(r"\b(fast\s+track|orphan\s+drug)\s+designation\b",  "FDA_DESIGNATION", 15, "long"),
    _r(r"\b510\(k\)\s+clearance\b",                        "FDA_510K",        15, "long"),
    _r(r"\bawarded?\s+\$\d",                                "BIG_AWARD",       18, "long"),
    _r(r"\b(government|defense|dod|nasa|darpa)\s+contract\b", "GOV_CONTRACT", 22, "long"),
    _r(r"\$\d{2,}\s*(million|m\b).*(contract|award|order)\b","BIG_CONTRACT",  18, "long"),
    _r(r"\bsigns?\s+(definitive\s+)?agreement\s+to\s+be\s+acquired\b", "BUYOUT_TARGET", 35, "long"),
    _r(r"\bto\s+be\s+acquired\s+(by|for)\b",               "ACQUIRED",        30, "long"),
    _r(r"\bmerger\s+agreement\b",                          "MERGER",          22, "long"),
    _r(r"\b(strategic\s+)?partnership\s+with\b",           "PARTNERSHIP",     12, "long"),
    _r(r"\bbeats?\s+(estimates|expectations|consensus)\b", "EARNINGS_BEAT",   14, "long"),
    _r(r"\braises?\s+(full[-\s]year\s+)?guidance\b",       "RAISES_GUIDANCE", 16, "long"),
    _r(r"\b(record|all[-\s]time\s+high)\s+(revenue|earnings|sales)\b", "RECORD_REV", 14, "long"),
    _r(r"\buplist(ing|ed)?\s+to\s+(nasdaq|nyse)\b",        "UPLISTING",       18, "long"),
    _r(r"\bnasdaq\s+listing\s+approved\b",                 "UPLISTING",       18, "long"),
    _r(r"\b(announces?\s+)?(stock\s+)?buyback\b",          "BUYBACK",         10, "long"),

    # --- Mild / situational bullish ---
    _r(r"\binitiates?\s+coverage\s+with\s+buy\b",          "ANALYST_BUY",      8, "long"),
    _r(r"\bupgraded?\s+to\s+(buy|outperform|overweight)\b","UPGRADE",         10, "long"),
    _r(r"\bprice\s+target\s+(raised|increased)\b",         "PT_RAISE",         6, "long"),
    _r(r"\b(announces?\s+)?(strategic\s+)?investment\b",   "INVESTMENT",       8, "long"),
    _r(r"\bpatent\s+(granted|issued|allowance)\b",         "PATENT",           8, "long"),
    _r(r"\bfirst\s+patient\s+(dosed|enrolled)\b",          "FPI_FPE",          6, "long"),

    # --- Bearish ---
    _r(r"\bphase\s*3.*(fail|miss|disappoint|halted)\b",    "PHASE3_FAIL",    -32, "short"),
    _r(r"\bphase\s*2.*(fail|miss|disappoint|halted)\b",    "PHASE2_FAIL",    -22, "short"),
    _r(r"\bfda\s+(reject|crl|complete\s+response\s+letter)\b","FDA_REJECT", -30, "short"),
    _r(r"\bclinical\s+hold\b",                             "CLINICAL_HOLD",  -28, "short"),
    _r(r"\bmiss(es)?\s+(estimates|expectations|consensus)\b","EARNINGS_MISS",-16, "short"),
    _r(r"\b(lowers?|cuts?|reduces?)\s+(full[-\s]year\s+)?guidance\b","CUTS_GUIDANCE",-18, "short"),
    _r(r"\bdowngrade(d)?\s+to\s+(sell|underperform|underweight)\b","DOWNGRADE",-12, "short"),
    _r(r"\bgoing\s+concern\b",                             "GOING_CONCERN",  -22, "short"),
    _r(r"\bbankruptcy\b",                                  "BANKRUPTCY",     -35, "short"),
    _r(r"\b(restate|restating)\s+(financial|earnings)\b",  "RESTATEMENT",    -20, "short"),
    _r(r"\bsec\s+investigation\b",                         "SEC_INVESTIGATION",-25,"short"),
    _r(r"\bdoj\s+investigation\b",                         "DOJ_INVESTIGATION",-25,"short"),

    # --- Dilution (always bearish for longs, fuel for shorts) ---
    _r(r"\b(public|registered\s+direct|underwritten)\s+offering\b", "OFFERING",   -25, "short"),
    _r(r"\bat[-\s]the[-\s]market\s+(offering|facility)\b", "ATM_OFFERING",   -20, "short"),
    _r(r"\breverse\s+stock\s+split\b",                     "REVERSE_SPLIT",  -22, "short"),
    _r(r"\bprices?\s+\$\d.*\s+offering\b",                 "OFFERING_PRICED",-25, "short"),
    _r(r"\bequity\s+line\s+of\s+credit\b",                 "ELOC",           -18, "short"),
    _r(r"\bconvertible\s+(note|debenture)\b",              "CONVERT_NOTE",   -15, "short"),

    # --- Neutral catalysts (still useful as universe signal) ---
    _r(r"\b(reports|announces?)\s+(q[1-4]|first|second|third|fourth|fy)\s+(earnings|results)\b", "EARNINGS", 4, "neutral"),
    _r(r"\bphase\s*1\b",                                    "PHASE1",          4, "neutral"),
]


@dataclass
class NewsItem:
    symbol: str
    headline: str
    summary: str
    url: str
    source: str
    published_at: datetime
    matches: list[tuple[str, float, str]] = field(default_factory=list)  # (tag, weight, side)

    @property
    def tags(self) -> list[str]:
        return [m[0] for m in self.matches]

    @property
    def bullish_score(self) -> float:
        return sum(w for _, w, s in self.matches if s == "long" and w > 0)

    @property
    def bearish_score(self) -> float:
        return sum(-w for _, w, s in self.matches if s == "short" and w < 0)

    @property
    def primary_side(self) -> Optional[str]:
        if self.bullish_score >= self.bearish_score and self.bullish_score > 0:
            return "long"
        if self.bearish_score > self.bullish_score:
            return "short"
        return None

    @property
    def is_dilutive(self) -> bool:
        return any(t in {"OFFERING", "OFFERING_PRICED", "ATM_OFFERING", "ELOC",
                         "CONVERT_NOTE", "REVERSE_SPLIT"} for t in self.tags)

    @property
    def top_tag(self) -> Optional[str]:
        if not self.matches:
            return None
        return max(self.matches, key=lambda m: abs(m[1]))[0]


def classify_text(text: str) -> list[tuple[str, float, str]]:
    return [(r.tag, r.weight, r.side) for r in CATALYST_RULES if r.pattern.search(text)]


def _client() -> Optional[finnhub.Client]:
    if not CONFIG.finnhub_key:
        return None
    return finnhub.Client(api_key=CONFIG.finnhub_key)


def _from_finnhub(symbol: str, hours: int) -> list[NewsItem]:
    client = _client()
    if client is None:
        return []
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=max(1, hours // 24))
    try:
        raw = client.company_news(symbol, _from=start.isoformat(), to=end.isoformat())
    except Exception:
        return []
    items: list[NewsItem] = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    for r in raw:
        ts = datetime.fromtimestamp(r.get("datetime", 0), tz=timezone.utc)
        if ts < cutoff:
            continue
        text = f"{r.get('headline', '')} {r.get('summary', '')}"
        items.append(NewsItem(
            symbol=symbol.upper(),
            headline=r.get("headline", ""),
            summary=(r.get("summary", "") or "")[:280],
            url=r.get("url", ""),
            source=r.get("source", ""),
            published_at=ts,
            matches=classify_text(text),
        ))
    return items


def _from_pr_wires(symbol: str, hours: int) -> list[NewsItem]:
    """Pull PR wire items already filtered by symbol. Imported lazily to
    avoid a circular import at module load time."""
    from .prwires import fetch_pr_items, pr_items_by_symbol
    pr_items = pr_items_by_symbol(fetch_pr_items(hours=hours))
    out: list[NewsItem] = []
    for it in pr_items.get(symbol.upper(), []):
        text = f"{it.headline} {it.summary}"
        out.append(NewsItem(
            symbol=symbol.upper(),
            headline=it.headline,
            summary=it.summary,
            url=it.url,
            source=it.source,
            published_at=it.published_at,
            matches=classify_text(text),
        ))
    return out


def fetch_recent_news(symbol: str, hours: int = 24) -> list[NewsItem]:
    items = _from_finnhub(symbol, hours) + _from_pr_wires(symbol, hours)
    # Deduplicate on URL
    seen: set[str] = set()
    out: list[NewsItem] = []
    for it in items:
        key = it.url or it.headline
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    out.sort(key=lambda x: x.published_at, reverse=True)
    return out


def has_catalyst(symbol: str, hours: int = 24) -> tuple[bool, list[NewsItem]]:
    items = fetch_recent_news(symbol, hours=hours)
    tagged = [i for i in items if i.matches]
    return bool(tagged), tagged
