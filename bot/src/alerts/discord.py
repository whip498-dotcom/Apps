"""Discord webhook alerter — side-aware embeds with entry/SL/TP and levels."""
from __future__ import annotations

import json
from typing import Iterable, Optional

import requests

from ..config import CONFIG
from ..scanner.scanner import Candidate

# Title prefix + color per (side, kind)
LONG_GREEN = 0x2ECC71
SHORT_RED = 0xE74C3C
UPDATE_BLUE = 0x3498DB
UPDATE_PURPLE = 0x9B59B6
UPDATE_YELLOW = 0xF1C40F
WARN_ORANGE = 0xE67E22

KIND_HEADER = {
    "new":        "Premarket scan",
    "price_up":   "Price up update",
    "price_down": "Price down update",
    "new_filing": "New filing",
    "vol_surge":  "Volume surge",
}


def _color(c: Candidate, kind: str) -> int:
    if kind == "price_up":   return UPDATE_BLUE
    if kind == "price_down": return UPDATE_BLUE
    if kind == "new_filing": return UPDATE_PURPLE
    if kind == "vol_surge":  return UPDATE_YELLOW
    # First alert — color by side, with dilution caution overlay for longs
    if c.side == "short":
        return SHORT_RED
    if c.has_dilution_risk:
        return WARN_ORANGE
    return LONG_GREEN


def _title(c: Candidate, kind: str) -> str:
    arrow = ""
    if kind == "price_up":   arrow = "↗ "
    elif kind == "price_down": arrow = "↘ "
    elif kind == "new_filing": arrow = "📄 "
    elif kind == "vol_surge":  arrow = "⚡ "
    side_tag = "LONG" if c.side == "long" else "SHORT"
    setup = f" · {c.setup}" if c.setup else ""
    return f"{arrow}${c.symbol} — {side_tag}{setup}"


def _trade_plan_field(c: Candidate) -> Optional[dict]:
    lv = c.levels
    if lv is None:
        return None
    risk = lv.risk_per_share
    risk_pct = (risk / lv.entry_mid * 100) if lv.entry_mid else 0
    body = (
        f"**Entry:** ${lv.entry_low:.2f} – ${lv.entry_high:.2f}\n"
        f"**Stop:**  ${lv.stop:.2f}  (risk ${risk:.2f} / {risk_pct:.1f}%)\n"
        f"**TP1:**   ${lv.target_1:.2f}  (R:R {lv.rr_target_1:.2f})\n"
        f"**TP2:**   ${lv.target_2:.2f}  (R:R {lv.rr_target_2:.2f})"
    )
    return {"name": "Trade plan", "value": body, "inline": False}


def _levels_field(c: Candidate) -> Optional[dict]:
    lv = c.levels
    if lv is None:
        return None
    body = (
        f"PMH ${lv.premarket_high:.2f} · PML ${lv.premarket_low:.2f} · "
        f"VWAP ${lv.vwap:.2f}\n"
        f"PDH ${lv.prior_day_high:.2f} · PDC ${lv.prior_day_close:.2f} · "
        f"PDL ${lv.prior_day_low:.2f}\n"
        f"R2 ${lv.r2:.2f} · R1 ${lv.r1:.2f} · Pivot ${lv.pivot:.2f} · "
        f"S1 ${lv.s1:.2f} · S2 ${lv.s2:.2f}"
    )
    return {"name": "Levels", "value": body, "inline": False}


def _embed_for(c: Candidate, kind: str = "new", initial_price: Optional[float] = None) -> dict:
    fields: list[dict] = [
        {"name": "Price", "value": f"${c.quote.last:.2f}", "inline": True},
        {"name": "Gap",   "value": f"{c.quote.gap_pct:+.1f}%", "inline": True},
        {"name": "RVol",  "value": f"{c.quote.relative_volume:.1f}x", "inline": True},
        {"name": "PM Vol","value": f"{c.quote.premarket_volume:,}", "inline": True},
        {"name": "Float", "value": f"{c.float_shares/1_000_000:.1f}M" if c.float_shares else "?", "inline": True},
        {"name": "Score", "value": f"{c.score:.1f}", "inline": True},
    ]

    if initial_price is not None and kind != "new":
        delta_pct = (c.quote.last - initial_price) / initial_price * 100
        fields.append({
            "name": "Since first alert",
            "value": f"${initial_price:.2f} → ${c.quote.last:.2f} ({delta_pct:+.1f}%)",
            "inline": False,
        })

    plan = _trade_plan_field(c)
    if plan: fields.append(plan)

    if c.catalysts:
        top = c.catalysts[0]
        tags = " ".join(f"`{t}`" for t in top.tags) if top.tags else "news"
        fields.append({
            "name": f"Catalyst {tags}",
            "value": f"[{top.headline[:200]}]({top.url})",
            "inline": False,
        })

    if c.filings:
        f = c.filings[0]
        marker = "⚠️ " if f.is_dilutive else ""
        fields.append({
            "name": f"{marker}Filing — {f.form}",
            "value": f"[{f.title[:200]}]({f.link})",
            "inline": False,
        })

    levels = _levels_field(c)
    if levels: fields.append(levels)

    if c.flags:
        fields.append({"name": "Flags", "value": ", ".join(c.flags), "inline": False})

    return {
        "title": _title(c, kind),
        "color": _color(c, kind),
        "fields": fields,
        "footer": {"text": "Premarket scanner — not financial advice"},
    }


def _post(payload: dict) -> bool:
    if not CONFIG.discord_webhook:
        return False
    try:
        r = requests.post(
            CONFIG.discord_webhook,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        return r.status_code in (200, 204)
    except Exception:
        return False


def _chunked(items: list, size: int = 10):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def send_candidates(candidates: Iterable[Candidate], top_n: int = 10) -> bool:
    cs = list(candidates)[:top_n]
    if not cs:
        return False
    longs = [c for c in cs if c.side == "long"]
    shorts = [c for c in cs if c.side == "short"]
    summary_bits = []
    if longs: summary_bits.append(f"{len(longs)} LONG")
    if shorts: summary_bits.append(f"{len(shorts)} SHORT")
    summary = " · ".join(summary_bits)

    ok = True
    for batch in _chunked(cs, 10):
        ok &= _post({
            "username": "Premarket Scanner",
            "content": f"**Premarket scan — {summary}**",
            "embeds": [_embed_for(c, kind="new") for c in batch],
        })
    return ok


def send_updates(updates: list[tuple[Candidate, str, Optional[float]]]) -> bool:
    if not updates:
        return False
    headers: dict[str, list] = {}
    for tup in updates:
        headers.setdefault(tup[1], []).append(tup)
    summary = " · ".join(f"{KIND_HEADER.get(k, k)} ({len(v)})" for k, v in headers.items())

    ok = True
    for batch in _chunked(updates, 10):
        ok &= _post({
            "username": "Premarket Scanner",
            "content": f"**Update — {summary}**",
            "embeds": [_embed_for(c, kind=kind, initial_price=ip) for c, kind, ip in batch],
        })
    return ok


def send_text(message: str) -> bool:
    return _post({"content": message})
