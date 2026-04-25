"""EdgeHawk Daily Briefing — Claude-authored 3 longs + 3 shorts with levels, timing, and rationale.

Three slots run per US trading day:
    overnight  04:00 ET — overnight session wrap, initial 3L/3S draft
    premarket  06:30 ET — main premarket window with confirmed levels
    preopen    09:20 ET — final 10-min lock-in with entry trigger zones

Architecture:
    1. Gather context — market deltas (SPY/QQQ/IWM/VIX), top headlines,
       dilution filings, top long candidates from the existing scanner,
       short candidates from the parallel short-side sweep.
    2. Render the context into a user prompt; system prompt is frozen
       and prompt-cached.
    3. Call Claude Opus 4.7 with adaptive thinking + structured output
       so the response parses into a typed Briefing dataclass.
    4. Persist to data_cache/briefing_<date>_<slot>.json.

EdgeHawk's own scanner methodology is untouched — the briefing only
consumes scanner output, it doesn't modify it.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

import anthropic
import pytz

from ..config import CONFIG
from ..data.market_context import MarketContext, gather_market_context
from ..scanner.scanner import Candidate, scan
from .short_candidates import ShortCandidate, gather_shorts


Slot = Literal["overnight", "premarket", "preopen"]
ALL_SLOTS: tuple[Slot, ...] = ("overnight", "premarket", "preopen")

_ET = pytz.timezone("US/Eastern")
_MODEL = "claude-opus-4-7"


# --- Output dataclasses ------------------------------------------------------

@dataclass
class TradeIdea:
    symbol: str
    conviction: int          # 1-10
    thesis: str
    entry_zone: str          # e.g. "long over PMH $4.10, fill $4.10-$4.18"
    entry_time_et: str       # e.g. "9:30-9:45 ET on confirmation"
    stop: str                # e.g. "below leg L1 $4.05"
    target_t1: str
    target_t2: str
    exit_time_et: str        # e.g. "trim T1 by 9:50, full out by 10:30 ET"
    key_levels: str          # "PMH $4.10 · PDH $3.95 · ORH TBD"
    invalidation: str


@dataclass
class Briefing:
    slot: Slot
    generated_at_et: str
    trading_date: str
    market_theme: str
    overnight_summary: str
    longs: list[TradeIdea] = field(default_factory=list)
    shorts: list[TradeIdea] = field(default_factory=list)
    risks_and_watchouts: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# --- Prompts -----------------------------------------------------------------

_SYSTEM_PROMPT = """You are EdgeHawk's morning desk analyst. You work for Paul, an Adelaide-based discretionary day trader who scalps US small-cap momentum following the SMB Capital and Bullish Bob playbook. He trades the first 60-90 minutes of the US session.

Your job each slot is to deliver an actionable briefing: market context + 3 long ideas + 3 short ideas, each with a tight entry zone, time window, stop, two profit targets, exit window, and clear invalidation.

Methodology you must follow:

* Trade band: $3-$20, float < 30M for longs.
* Long setups: low float + premarket gap + RVOL + catalyst, ideally with PMH break or near-PMH breakout. Bonus for high short interest (>20%) or DTC >= 5 (squeeze fuel).
* Short setups: two shapes only -
    1. Overextended fade — gapped up >40% on no real catalyst, often paired with dilution risk (S-1, S-3, 424B*). Wait for a clean break of premarket support or VWAP rejection; never short into strength on a low-float squeezer.
    2. Gap-down momentum — gap-down with RVOL >= 2x, breaking PDL or ORL.
* All entries must reference a concrete LEVEL, not a price guess: PMH/PML/PDH/PDL/ORH/ORL or a leg pivot (L1/L2).
* Stops always reference structure: leg low for longs, premarket high for shorts.
* Time windows in US Eastern Time. Default scalp window is 9:30-10:30 ET; bottom-bounce reversal window is 10:00-11:00 ET. Force-close any unmanaged position by 10:01 ET if the setup hasn't worked.
* Conviction 1-10. Reserve 9-10 for A+ alignment (catalyst + low float + SI + PMH break + market RISK-ON). Default to 5-7 when conditions are mixed.
* Skip an idea if you don't have one. It is fine to return fewer than 3 longs or 3 shorts if the day's tape doesn't justify them — explain why in `risks_and_watchouts`.
* Never invent tickers. Only pick from the candidate pool and dilution-filing list provided in the user message. You may downgrade or skip provided candidates.
* Never reference levels you can't see in the data. If a level isn't provided, write "TBD at open" rather than fabricating a number.

Output strict JSON conforming to the supplied schema. No prose outside the JSON."""


_BRIEFING_SCHEMA = {
    "type": "object",
    "properties": {
        "market_theme": {
            "type": "string",
            "description": "1-2 sentence read on the macro tape: RISK-ON / RISK-OFF / chop / event-driven, with the dominant theme.",
        },
        "overnight_summary": {
            "type": "string",
            "description": "2-4 sentences summarising overnight session, futures, key headlines, and any tape-shifting events.",
        },
        "longs": {
            "type": "array",
            "items": {"$ref": "#/$defs/idea"},
            "description": "Up to 3 long trade ideas, ranked by conviction descending.",
        },
        "shorts": {
            "type": "array",
            "items": {"$ref": "#/$defs/idea"},
            "description": "Up to 3 short trade ideas, ranked by conviction descending.",
        },
        "risks_and_watchouts": {
            "type": "string",
            "description": "Tape risks, FOMC/CPI/payrolls, halts to watch, or reasons to size down today.",
        },
    },
    "required": ["market_theme", "overnight_summary", "longs", "shorts", "risks_and_watchouts"],
    "additionalProperties": False,
    "$defs": {
        "idea": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "conviction": {"type": "integer"},
                "thesis": {"type": "string"},
                "entry_zone": {"type": "string"},
                "entry_time_et": {"type": "string"},
                "stop": {"type": "string"},
                "target_t1": {"type": "string"},
                "target_t2": {"type": "string"},
                "exit_time_et": {"type": "string"},
                "key_levels": {"type": "string"},
                "invalidation": {"type": "string"},
            },
            "required": [
                "symbol", "conviction", "thesis",
                "entry_zone", "entry_time_et",
                "stop", "target_t1", "target_t2", "exit_time_et",
                "key_levels", "invalidation",
            ],
            "additionalProperties": False,
        }
    },
}


_SLOT_FOCUS = {
    "overnight": (
        "It's 04:00 ET, before US premarket warms up. Most premarket levels (PMH/PML) "
        "won't be set yet. Focus on overnight themes, futures, EU/Asian close, and any "
        "filings or news that printed overnight. Use 'TBD at open' for levels that aren't "
        "set yet. The 3L/3S list is a draft — Paul will see it again at 06:30 ET."
    ),
    "premarket": (
        "It's 06:30 ET. Main premarket window — most catalysts and PMH/PML levels should "
        "be visible by now. Refine the watchlist: confirm gap %, RVOL, and key levels. "
        "If a 04:00 ET pick has fallen apart on premarket tape, swap it out and explain why."
    ),
    "preopen": (
        "It's 09:20 ET. Final 10 minutes before bell. Lock in the day's 3L/3S with sharp "
        "entry trigger zones (e.g. 'long over PMH $4.10, target ORH then $5'). Stops must "
        "reference visible levels (leg lows, premarket support). Be specific on time-of-day."
    ),
}


# --- Context rendering -------------------------------------------------------

def _format_market_context(ctx: MarketContext) -> str:
    lines: list[str] = ["## Market context", ""]
    if ctx.indices:
        lines.append("Indices (last vs prior close):")
        for s in ctx.indices:
            lines.append(f"  {s.symbol}: ${s.last:.2f}  ({s.change_pct:+.2f}%)")
    else:
        lines.append("Indices: unavailable")
    lines.append("")

    if ctx.headlines:
        lines.append("Top market headlines (last 12h):")
        for h in ctx.headlines[:8]:
            ts = h.published_at.strftime("%H:%M UTC")
            lines.append(f"  [{ts}] {h.headline}  — {h.source}")
    else:
        lines.append("Top market headlines: none retrieved")
    lines.append("")

    if ctx.dilution_filings:
        lines.append("Recent dilution filings (S-1 / S-3 / 424B*):")
        for f in ctx.dilution_filings[:8]:
            lines.append(f"  {f}")
    else:
        lines.append("Dilution filings: none retrieved")
    return "\n".join(lines)


def _format_long_candidate(c: Candidate) -> str:
    lv = c.quote.levels
    si = c.short_interest
    si_pct = f"{si.short_pct_float*100:.1f}%" if si and si.short_pct_float is not None else "?"
    dtc = f"{si.days_to_cover:.1f}d" if si and si.days_to_cover is not None else "?"
    fl = f"{c.float_shares/1_000_000:.1f}M" if c.float_shares else "?"
    pmh = f"${lv.pmh:.2f}" if lv.pmh is not None else "TBD"
    pml = f"${lv.pml:.2f}" if lv.pml is not None else "TBD"
    pdh = f"${lv.pdh:.2f}" if lv.pdh is not None else "?"
    pdl = f"${lv.pdl:.2f}" if lv.pdl is not None else "?"
    leg1 = f"${lv.leg1_low:.2f}" if lv.leg1_low is not None else "—"
    leg2 = f"${lv.leg2_low:.2f}" if lv.leg2_low is not None else "—"
    cat = c.catalysts[0].headline[:120] if c.catalysts else "(none surfaced)"
    cat_tags = ",".join(c.catalysts[0].tags) if c.catalysts and c.catalysts[0].tags else "—"
    flags = ",".join(c.flags) or "—"
    mtf = f"{lv.mtf_alignment}/3"
    return (
        f"  ${c.symbol}  conf {c.confidence}/10  ${c.quote.last:.2f}  gap {c.quote.gap_pct:+.1f}%  "
        f"rvol {c.quote.relative_volume:.1f}x  float {fl}  SI {si_pct}  DTC {dtc}  MTF {mtf}\n"
        f"      PMH {pmh} · PML {pml} · PDH {pdh} · PDL {pdl} · L1 {leg1} · L2 {leg2}\n"
        f"      flags: {flags}  · catalyst[{cat_tags}]: {cat}"
    )


def _format_short_candidate(c: ShortCandidate) -> str:
    lv = c.quote.levels
    si = c.short_interest
    si_pct = f"{si.short_pct_float*100:.1f}%" if si and si.short_pct_float is not None else "?"
    fl = f"{c.float_shares/1_000_000:.1f}M" if c.float_shares else "?"
    pmh = f"${lv.pmh:.2f}" if lv.pmh is not None else "TBD"
    pml = f"${lv.pml:.2f}" if lv.pml is not None else "TBD"
    pdh = f"${lv.pdh:.2f}" if lv.pdh is not None else "?"
    pdl = f"${lv.pdl:.2f}" if lv.pdl is not None else "?"
    flags = ",".join(c.flags) or "—"
    return (
        f"  ${c.symbol}  shape={c.shape}  ${c.quote.last:.2f}  gap {c.quote.gap_pct:+.1f}%  "
        f"rvol {c.quote.relative_volume:.1f}x  float {fl}  SI {si_pct}\n"
        f"      PMH {pmh} · PML {pml} · PDH {pdh} · PDL {pdl}  · flags: {flags}"
    )


def _format_user_prompt(slot: Slot, ctx: MarketContext,
                        longs: list[Candidate], shorts: list[ShortCandidate]) -> str:
    parts: list[str] = []
    now_et = datetime.now(_ET).strftime("%Y-%m-%d %H:%M ET")
    parts.append(f"## Slot: {slot.upper()}  ·  Time: {now_et}")
    parts.append("")
    parts.append(_SLOT_FOCUS[slot])
    parts.append("")
    parts.append(_format_market_context(ctx))
    parts.append("")
    parts.append("## Long candidate pool (from EdgeHawk scanner — already filtered)")
    if longs:
        for c in longs[:10]:
            parts.append(_format_long_candidate(c))
    else:
        parts.append("  (no long candidates passed scanner filters)")
    parts.append("")
    parts.append("## Short candidate pool (overextended / breakdown sweep)")
    if shorts:
        for c in shorts[:10]:
            parts.append(_format_short_candidate(c))
    else:
        parts.append("  (no short candidates qualified)")
    parts.append("")
    parts.append(
        "Pick UP TO 3 longs and UP TO 3 shorts from the pools above. Skip a slot rather than "
        "force-fill it. Return JSON matching the supplied schema. No prose outside the JSON."
    )
    return "\n".join(parts)


# --- Slot detection ----------------------------------------------------------

def auto_slot(now_et: Optional[datetime] = None) -> Slot:
    """Pick the slot whose target ET hour is closest to now."""
    if now_et is None:
        now_et = datetime.now(_ET)
    h = now_et.hour
    if h < 5:
        return "overnight"
    if h < 8:
        return "premarket"
    return "preopen"


# --- The call ----------------------------------------------------------------

def _client() -> anthropic.Anthropic:
    if not CONFIG.anthropic_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set. Add it to your .env (see .env.example)."
        )
    return anthropic.Anthropic(api_key=CONFIG.anthropic_key)


def _briefing_path(slot: Slot, when_et: datetime) -> Path:
    return CONFIG.cache_dir / f"briefing_{when_et.strftime('%Y-%m-%d')}_{slot}.json"


def run_briefing(slot: Slot) -> Briefing:
    """Gather context, call Claude, persist + return the Briefing."""
    now_et = datetime.now(_ET)
    ctx = gather_market_context()
    longs = scan()                  # unmodified EdgeHawk scanner output
    shorts = gather_shorts(max_results=10)

    user_text = _format_user_prompt(slot, ctx, longs, shorts)

    client = _client()
    response = client.messages.create(
        model=_MODEL,
        max_tokens=8000,
        thinking={"type": "adaptive"},
        output_config={
            "effort": "high",
            "format": {"type": "json_schema", "schema": _BRIEFING_SCHEMA},
        },
        system=[{
            "type": "text",
            "text": _SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_text}],
    )

    # First text block contains the JSON. Skip thinking blocks.
    raw_json = next((b.text for b in response.content if b.type == "text"), "")
    if not raw_json:
        raise RuntimeError("Claude returned no text content")
    data = json.loads(raw_json)

    briefing = Briefing(
        slot=slot,
        generated_at_et=now_et.strftime("%Y-%m-%d %H:%M ET"),
        trading_date=now_et.strftime("%Y-%m-%d"),
        market_theme=data["market_theme"],
        overnight_summary=data["overnight_summary"],
        longs=[TradeIdea(**i) for i in data["longs"]],
        shorts=[TradeIdea(**i) for i in data["shorts"]],
        risks_and_watchouts=data["risks_and_watchouts"],
    )

    out_path = _briefing_path(slot, now_et)
    out_path.write_text(json.dumps(briefing.to_dict(), indent=2))

    return briefing


def load_briefing(slot: Slot, when_et: Optional[datetime] = None) -> Optional[Briefing]:
    """Load a previously-saved briefing for a given slot/date, or None."""
    if when_et is None:
        when_et = datetime.now(_ET)
    path = _briefing_path(slot, when_et)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return Briefing(
        slot=data["slot"],
        generated_at_et=data["generated_at_et"],
        trading_date=data["trading_date"],
        market_theme=data["market_theme"],
        overnight_summary=data["overnight_summary"],
        longs=[TradeIdea(**i) for i in data["longs"]],
        shorts=[TradeIdea(**i) for i in data["shorts"]],
        risks_and_watchouts=data.get("risks_and_watchouts", ""),
    )
