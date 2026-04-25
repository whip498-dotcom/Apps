"""Discord webhook alerter for premarket scanner hits."""
from __future__ import annotations

import json
from typing import Iterable

import requests

from ..config import CONFIG
from ..scanner.scanner import Candidate


def _embed_for(c: Candidate) -> dict:
    # Color by confidence so the eye sorts the embeds at a glance.
    if c.confidence >= 9:
        color = 0x2ECC71  # green — A+
    elif c.confidence >= 7:
        color = 0xF1C40F  # yellow — watch
    else:
        color = 0x95A5A6  # gray — meh
    if c.has_dilution_risk:
        color = 0xE67E22  # orange overrides — caution

    si = c.short_interest
    si_pct = f"{si.short_pct_float*100:.1f}%" if si and si.short_pct_float is not None else "?"
    dtc = f"{si.days_to_cover:.1f}d" if si and si.days_to_cover is not None else "?"

    fields = [
        {"name": "Confidence", "value": f"**{c.confidence}/10**", "inline": True},
        {"name": "Price", "value": f"${c.quote.last:.2f}", "inline": True},
        {"name": "Gap", "value": f"+{c.quote.gap_pct:.1f}%", "inline": True},
        {"name": "RVol", "value": f"{c.quote.relative_volume:.1f}x", "inline": True},
        {"name": "PM Vol", "value": f"{c.quote.premarket_volume:,}", "inline": True},
        {
            "name": "Float",
            "value": f"{c.float_shares/1_000_000:.1f}M" if c.float_shares else "?",
            "inline": True,
        },
        {"name": "Short Int", "value": si_pct, "inline": True},
        {"name": "Days to Cover", "value": dtc, "inline": True},
        {"name": "Score", "value": f"{c.score:.1f}", "inline": True},
    ]

    lv = c.quote.levels
    level_bits = []
    if lv.pmh is not None:
        level_bits.append(f"PMH `${lv.pmh:.2f}`")
    if lv.pml is not None:
        level_bits.append(f"PML `${lv.pml:.2f}`")
    if lv.pdh is not None:
        level_bits.append(f"PDH `${lv.pdh:.2f}`")
    if lv.pdl is not None:
        level_bits.append(f"PDL `${lv.pdl:.2f}`")
    if lv.orh is not None:
        level_bits.append(f"ORH `${lv.orh:.2f}`")
    if level_bits:
        fields.append({"name": "Key Levels", "value": " · ".join(level_bits), "inline": False})

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

    if c.flags:
        fields.append({"name": "Flags", "value": ", ".join(c.flags), "inline": False})

    return {
        "title": f"${c.symbol}",
        "color": color,
        "fields": fields,
        "footer": {"text": "EdgeHawk — not financial advice"},
    }


def send_candidates(candidates: Iterable[Candidate], top_n: int = 10) -> bool:
    """Posts the top candidates as one Discord message. Returns True on success."""
    if not CONFIG.discord_webhook:
        return False
    cs = list(candidates)[:top_n]
    if not cs:
        return False

    avg_conf = sum(c.confidence for c in cs) / len(cs)
    payload = {
        "username": "EdgeHawk",
        "content": f"**EdgeHawk — {len(cs)} squeeze candidate(s) · avg conf {avg_conf:.1f}/10**",
        "embeds": [_embed_for(c) for c in cs],
    }

    r = requests.post(
        CONFIG.discord_webhook,
        data=json.dumps(payload),
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    return r.status_code in (200, 204)


def send_text(message: str) -> bool:
    if not CONFIG.discord_webhook:
        return False
    r = requests.post(
        CONFIG.discord_webhook,
        json={"content": message},
        timeout=10,
    )
    return r.status_code in (200, 204)
