"""F8 thesis-check — pre-entry pressure-test of a planned trade.

Hot path: Paul has eyes on the tape, finger on buy. He hits the F8 key
(bound to ``python -m src.cli thesis ...``) and gets a 4-line verdict
back in under 2 seconds:

    VERDICT: GO | WAIT | SKIP   (quality 0-10, R:R)
    WORKS:   <one reason it pays>
    BREAKS:  <one reason it loses>
    SIZE:    <sanity check vs sizing module>

Architecture
------------
* Frozen system prompt (Paul's profile + verdict rubric + schema doc)
  cached via ``cache_control: ephemeral`` so each F8 hit is read-only
  on the prefix — pays full price once, ~0.1x thereafter.
* Volatile per-request context (live quote, key levels, news, short
  interest, market deltas, similar past trades from the journal,
  sizing recommendation) goes in the user message — never poisons
  the cache.
* Sonnet 4.6 with adaptive thinking + ``effort: medium`` — the deeper
  Opus 4.7 work lives in the daily briefing; this surface is
  glanceable confirmation, not deep analysis.
* Structured output via ``output_config.format`` so the response
  parses straight into a typed dataclass.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import anthropic

from ..config import CONFIG
from ..data.float_data import get_float
from ..data.market_context import gather_market_context
from ..data.news import NewsItem, fetch_recent_news
from ..data.price import Quote, fetch_quote
from ..data.short_interest import ShortInterest, get_short_interest
from ..journal.journal import Trade, all_trades, trade_pnl
from ..sizing.sizing import SizeRecommendation, size_trade

_MODEL = "claude-sonnet-4-6"


# --- Output dataclass --------------------------------------------------------

@dataclass
class Verdict:
    verdict: str          # GO | WAIT | SKIP
    quality_score: float  # 0-10
    rr_ratio: float       # reward:risk to first target
    works: str            # one-line: top reason it pays
    breaks: str           # one-line: top reason it loses
    size_note: str        # one-line: sizing/risk sanity
    latency_ms: int = 0

    def color(self) -> str:
        return {"GO": "green", "WAIT": "yellow", "SKIP": "red"}.get(
            self.verdict.upper(), "white"
        )


@dataclass
class BattleCard:
    """Snapshot of everything Claude sees about the trade."""
    symbol: str
    entry: float
    stop: float
    shares: Optional[int] = None
    setup: Optional[str] = None
    catalyst_note: Optional[str] = None
    quote: Optional[Quote] = None
    news: list[NewsItem] = field(default_factory=list)
    short_interest: Optional[ShortInterest] = None
    float_shares: Optional[int] = None
    market: Optional[object] = None
    similar_trades: list[Trade] = field(default_factory=list)
    sizing: Optional[SizeRecommendation] = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# --- Schema for structured output --------------------------------------------

_VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["GO", "WAIT", "SKIP"],
            "description": "GO: take it as planned. WAIT: setup needs one more confirmation. SKIP: don't take this trade.",
        },
        "quality_score": {
            "type": "number",
            "minimum": 0,
            "maximum": 10,
            "description": "0-10 setup quality on the SMB / Bullish-Bob rubric (catalyst, float, RVOL, level proximity, MTF alignment, leg structure).",
        },
        "rr_ratio": {
            "type": "number",
            "description": "Reward to risk to the nearest realistic target (T1). Risk = entry - stop. Reward = next overhead level - entry.",
        },
        "works": {
            "type": "string",
            "description": "Single sentence, ≤120 chars: the strongest reason this setup pays.",
        },
        "breaks": {
            "type": "string",
            "description": "Single sentence, ≤120 chars: the most likely reason this fails (overhead supply, dilution risk, lack of catalyst freshness, etc).",
        },
        "size_note": {
            "type": "string",
            "description": "Single sentence, ≤120 chars: comment on the proposed share size vs the sizing module's recommendation and account risk policy.",
        },
    },
    "required": ["verdict", "quality_score", "rr_ratio", "works", "breaks", "size_note"],
    "additionalProperties": False,
}


# --- Frozen system prompt (cached) -------------------------------------------

_SYSTEM_PROMPT = """You are EdgeHawk's live trading copilot for Paul, an Adelaide-based discretionary day trader scalping the first 60-90 minutes of the US session from his home desk. He is hitting F8 right before clicking buy, asking you to pressure-test his planned trade in under 2 seconds.

# Paul's edge
- US premarket small-cap momentum, $1-$20, float < 50M, long-biased
- SMB Capital methodology — A+ confluence, FPB (first pullback break) vol-validated, leg-level stops
- Bullish-Bob squeeze model — high SI%, days-to-cover ≥ 5, low float catalysts
- Quality > quantity: he passes 9 trades to take 1 setup
- Risk policy: 2% account risk per trade, 25% max position size
- Hard exits: stop hit, MACD bear cross at level, bag-holder zone break, RVOL fade

# How you think
You are NOT his cheerleader. Default to scepticism — most premarket runners fail at PMH on the first attempt; most catalysts are stale or dilutive within hours; most "A+ setups" he sees are B-grade after the fact. Your job is to surface the one thing he's likely missing.

For VERDICT:
- GO     — A+ confluence is real (catalyst <12h, RVOL ≥3x, near a strong pivot, MTF aligned, 2R+ to T1, no dilution risk), and his proposed size is within policy.
- WAIT   — Setup is forming but needs one more confirmation (FPB not vol-validated yet, MACD pending bull cross, ORH not broken, etc).
- SKIP   — One or more red flags: overhead supply <1R away, dilutive filing in last 5 days, RVOL < 2x, news >24h old without fresh ignition, sizing breaches policy.

For QUALITY_SCORE (0-10):
Sum these factors, weight by importance:
- Catalyst freshness & strength (3 pts)
- Float / SI / DTC squeeze potential (2 pts)
- RVOL & volume confirmation (2 pts)
- Level proximity (near pivot, not extended) (1.5 pts)
- MTF alignment (1m/5m/15m bull) (0.75 pts)
- Leg structure / clean stop placement (0.75 pts)

For RR_RATIO:
Risk = entry - stop. Reward = nearest realistic overhead target (PMH if below, ORH, PDH, round number, or bag-holder zone) minus entry. If reward < risk, RR < 1 and that's a red flag — call it out in `breaks`.

For WORKS / BREAKS / SIZE_NOTE:
One sentence each. Concrete, level-specific, ≤120 chars. No hedging language. Reference actual prices, percentages, ticker names from the user message — not generic platitudes.

For SIZE_NOTE:
Compare Paul's proposed shares against the sizing module recommendation. Flag if:
- Proposed > sizing rec by >20%  → "size breach: <X> > rec <Y>"
- Risk_$ > 2% of equity          → "over policy: $<R> = <pct>%"
- Position > 25% of equity       → "over position cap"
- Otherwise                      → "within policy: <X> shares = $<R> risk = <pct>%"

Be brutally honest about size — he loses more money to oversizing than to bad setups."""


# --- Context gathering -------------------------------------------------------

def _similar_trades(symbol: str, setup: Optional[str], limit: int = 4) -> list[Trade]:
    """Same setup tag, most-recent first."""
    rows = all_trades()
    if setup:
        rows = [t for t in rows if t.setup == setup]
    return rows[:limit]


def gather_battle_card(
    symbol: str,
    entry: float,
    stop: float,
    shares: Optional[int],
    setup: Optional[str],
    catalyst_note: Optional[str],
    skip_market: bool = False,
) -> BattleCard:
    """Pull every datum Claude needs. Each lookup is best-effort — if a feed
    fails we still return a card so the thesis check doesn't block on a
    rate-limited yfinance call."""
    card = BattleCard(
        symbol=symbol.upper(),
        entry=entry,
        stop=stop,
        shares=shares,
        setup=setup,
        catalyst_note=catalyst_note,
    )

    try:
        card.quote = fetch_quote(symbol)
    except Exception:
        pass

    try:
        card.news = fetch_recent_news(symbol, hours=24)[:5]
    except Exception:
        pass

    try:
        card.short_interest = get_short_interest(symbol)
    except Exception:
        pass

    try:
        card.float_shares = get_float(symbol)
    except Exception:
        pass

    if not skip_market:
        try:
            card.market = gather_market_context()
        except Exception:
            pass

    card.similar_trades = _similar_trades(symbol, setup)

    try:
        card.sizing = size_trade(entry, stop, setup=setup)
    except Exception:
        pass

    return card


# --- Prompt rendering --------------------------------------------------------

def _fmt_levels(card: BattleCard) -> str:
    if card.quote is None:
        return "  (no live quote — yfinance unavailable)"
    q = card.quote
    lv = q.levels
    parts = [
        f"  last        ${q.last:.2f}",
        f"  prev_close  ${q.prev_close:.2f}",
        f"  gap%        {q.gap_pct:+.1f}%",
        f"  RVOL        {q.relative_volume:.2f}x  (PM vol {q.premarket_volume:,} / 30d avg {q.avg_volume_30d:,.0f})",
    ]
    if lv.pmh is not None:
        parts.append(f"  PMH         ${lv.pmh:.2f}")
    if lv.pml is not None:
        parts.append(f"  PML         ${lv.pml:.2f}")
    if lv.pdh is not None:
        parts.append(f"  PDH         ${lv.pdh:.2f}")
    if lv.pdl is not None:
        parts.append(f"  PDL         ${lv.pdl:.2f}")
    if lv.pdc is not None:
        parts.append(f"  PDC         ${lv.pdc:.2f}")
    if lv.orh is not None:
        parts.append(f"  ORH         ${lv.orh:.2f}")
    if lv.orl is not None:
        parts.append(f"  ORL         ${lv.orl:.2f}")
    if lv.leg1_low is not None:
        parts.append(f"  leg1_low    ${lv.leg1_low:.2f}")
    if lv.leg2_low is not None:
        parts.append(f"  leg2_low    ${lv.leg2_low:.2f}")
    mtf = (
        f"1m={'✓' if lv.mtf_1m_bull else '✗' if lv.mtf_1m_bull is False else '?'} "
        f"5m={'✓' if lv.mtf_5m_bull else '✗' if lv.mtf_5m_bull is False else '?'} "
        f"15m={'✓' if lv.mtf_15m_bull else '✗' if lv.mtf_15m_bull is False else '?'}"
    )
    parts.append(f"  MTF align   {mtf}  ({lv.mtf_alignment}/3)")
    return "\n".join(parts)


def _fmt_news(card: BattleCard) -> str:
    if not card.news:
        return "  (no recent news in last 24h)"
    out = []
    for n in card.news[:4]:
        age_h = (datetime.now(timezone.utc) - n.published_at).total_seconds() / 3600.0
        tags = "/".join(n.tags) if n.tags else "—"
        out.append(f"  [{age_h:4.1f}h ago, {tags}] {n.headline[:120]}")
    return "\n".join(out)


def _fmt_short_interest(card: BattleCard) -> str:
    si = card.short_interest
    if si is None or (si.short_pct_float is None and si.days_to_cover is None):
        return "  (short interest unavailable)"
    pct = f"{si.short_pct_float * 100:.1f}%" if si.short_pct_float is not None else "?"
    dtc = f"{si.days_to_cover:.1f}" if si.days_to_cover is not None else "?"
    short = f"{si.shares_short:,}" if si.shares_short is not None else "?"
    return f"  SI%={pct}  DTC={dtc}  shares_short={short}  squeeze_eligible={si.is_squeeze_candidate}"


def _fmt_market(card: BattleCard) -> str:
    if card.market is None:
        return "  (market context unavailable)"
    parts = []
    for s in card.market.indices:
        parts.append(f"{s.symbol} {s.change_pct:+.2f}%")
    return "  " + "  ·  ".join(parts) if parts else "  (no index data)"


def _fmt_similar(card: BattleCard) -> str:
    if not card.similar_trades:
        return "  (no prior trades for this setup tag)"
    out = []
    for t in card.similar_trades:
        pnl = trade_pnl(t)
        when = t.entry_time.strftime("%Y-%m-%d") if t.entry_time else "?"
        if pnl:
            out.append(
                f"  {when}  {t.symbol:<6} {t.setup:<20} "
                f"{t.shares}@${t.entry_price:.2f}→${t.exit_price:.2f}  "
                f"R={pnl.r_multiple:+.2f}"
            )
        else:
            out.append(
                f"  {when}  {t.symbol:<6} {t.setup:<20} "
                f"{t.shares}@${t.entry_price:.2f} (open)"
            )
    return "\n".join(out)


def _fmt_sizing(card: BattleCard) -> str:
    s = card.sizing
    if s is None:
        return "  (sizing unavailable — invalid entry/stop?)"
    proposed = card.shares
    risk_per_share = abs(card.entry - card.stop)
    proposed_risk = proposed * risk_per_share if proposed else 0.0
    proposed_risk_pct = (proposed_risk / CONFIG.account_equity * 100.0) if CONFIG.account_equity else 0.0
    proposed_str = (
        f"  proposed    {proposed} shares  "
        f"risk=${proposed_risk:.2f}  ({proposed_risk_pct:.2f}% of ${CONFIG.account_equity:,.0f})"
        if proposed
        else "  proposed    (none — using sizing rec)"
    )
    return (
        f"{proposed_str}\n"
        f"  recommend   {s.shares} shares  risk=${s.risk_dollars:.2f}  "
        f"position=${s.position_dollars:.2f}  via {s.method}\n"
        f"  policy      max_risk={CONFIG.max_risk_per_trade_pct:.1f}%  "
        f"max_position={CONFIG.max_position_size_pct:.0f}%"
    )


def render_user_prompt(card: BattleCard) -> str:
    risk_per_share = abs(card.entry - card.stop)
    direction = "long" if card.entry > card.stop else "short"
    return f"""# Trade plan
  symbol      {card.symbol}
  side        {direction}
  entry       ${card.entry:.4f}
  stop        ${card.stop:.4f}
  risk/share  ${risk_per_share:.4f}
  setup_tag   {card.setup or '(none)'}
  catalyst    {card.catalyst_note or '(see news below)'}
  shares      {card.shares if card.shares is not None else '(unspecified)'}

# Live quote & key levels
{_fmt_levels(card)}

# Float & short interest
  float       {f'{card.float_shares:,}' if card.float_shares else '(unknown)'}
{_fmt_short_interest(card)}

# Recent news (last 24h, sorted newest-first)
{_fmt_news(card)}

# Market context
{_fmt_market(card)}

# Similar past trades (same setup tag)
{_fmt_similar(card)}

# Sizing
{_fmt_sizing(card)}

Now produce the verdict JSON. Be concrete and reference the numbers above."""


# --- Main entry --------------------------------------------------------------

def _client() -> anthropic.Anthropic:
    if not CONFIG.anthropic_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set in .env — cannot run thesis check.")
    return anthropic.Anthropic(api_key=CONFIG.anthropic_key)


def thesis_check(
    symbol: str,
    entry: float,
    stop: float,
    shares: Optional[int] = None,
    setup: Optional[str] = None,
    catalyst_note: Optional[str] = None,
    skip_market: bool = False,
) -> tuple[Verdict, BattleCard]:
    """Pressure-test a planned trade. Returns (Verdict, BattleCard)."""
    started = datetime.now(timezone.utc)
    card = gather_battle_card(
        symbol=symbol,
        entry=entry,
        stop=stop,
        shares=shares,
        setup=setup,
        catalyst_note=catalyst_note,
        skip_market=skip_market,
    )
    user_text = render_user_prompt(card)

    client = _client()
    response = client.messages.create(
        model=_MODEL,
        max_tokens=2048,
        thinking={"type": "adaptive"},
        output_config={
            "format": {"type": "json_schema", "schema": _VERDICT_SCHEMA},
        },
        system=[{
            "type": "text",
            "text": _SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_text}],
    )

    raw = next((b.text for b in response.content if b.type == "text"), "")
    if not raw:
        raise RuntimeError("Claude returned no text content")
    data = json.loads(raw)
    elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    verdict = Verdict(
        verdict=data["verdict"],
        quality_score=float(data["quality_score"]),
        rr_ratio=float(data["rr_ratio"]),
        works=data["works"],
        breaks=data["breaks"],
        size_note=data["size_note"],
        latency_ms=elapsed_ms,
    )
    return verdict, card
