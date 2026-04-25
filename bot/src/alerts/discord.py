"""Discord webhook alerter for premarket scanner hits."""
from __future__ import annotations

import json
from typing import Iterable, Optional

import requests

from ..config import CONFIG
from ..scanner.scanner import Candidate

# Visual style per alert kind
KIND_STYLE = {
    "new":         {"color": 0x2ECC71, "title_prefix": "",        "header": "Premarket scan"},
    "price_up":    {"color": 0x3498DB, "title_prefix": "↗ ",      "header": "Price up update"},
    "price_down":  {"color": 0xE74C3C, "title_prefix": "↘ ",      "header": "Price down update"},
    "new_filing":  {"color": 0x9B59B6, "title_prefix": "📄 ",     "header": "New filing"},
    "vol_surge":   {"color": 0xF1C40F, "title_prefix": "⚡ ",     "header": "Volume surge"},
}


def _embed_for(c: Candidate, kind: str = "new", initial_price: Optional[float] = None) -> dict:
    style = KIND_STYLE.get(kind, KIND_STYLE["new"])
    color = style["color"]
    if kind == "new" and c.has_dilution_risk:
        color = 0xE67E22
    if kind == "new" and "NO_CATALYST" in c.flags:
        color = 0x95A5A6

    fields = [
        {"name": "Price", "value": f"${c.quote.last:.2f}", "inline": True},
        {"name": "Gap", "value": f"+{c.quote.gap_pct:.1f}%", "inline": True},
        {"name": "RVol", "value": f"{c.quote.relative_volume:.1f}x", "inline": True},
        {"name": "PM Vol", "value": f"{c.quote.premarket_volume:,}", "inline": True},
        {
            "name": "Float",
            "value": f"{c.float_shares/1_000_000:.1f}M" if c.float_shares else "?",
            "inline": True,
        },
        {"name": "Score", "value": f"{c.score:.1f}", "inline": True},
    ]

    if initial_price is not None and kind != "new":
        delta_pct = (c.quote.last - initial_price) / initial_price * 100
        fields.append({
            "name": "Since first alert",
            "value": f"${initial_price:.2f} → ${c.quote.last:.2f} ({delta_pct:+.1f}%)",
            "inline": False,
        })

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
        "title": f"{style['title_prefix']}${c.symbol}",
        "color": color,
        "fields": fields,
        "footer": {"text": "Premarket scanner — not financial advice"},
    }


def _post(payload: dict) -> bool:
    if not CONFIG.discord_webhook:
        return False
    r = requests.post(
        CONFIG.discord_webhook,
        data=json.dumps(payload),
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    return r.status_code in (200, 204)


def send_candidates(candidates: Iterable[Candidate], top_n: int = 10) -> bool:
    """Posts new (first-time) candidates as one Discord message."""
    cs = list(candidates)[:top_n]
    if not cs:
        return False
    payload = {
        "username": "Premarket Scanner",
        "content": f"**Premarket scan — {len(cs)} new candidate(s)**",
        "embeds": [_embed_for(c, kind="new") for c in cs],
    }
    return _post(payload)


def send_updates(updates: list[tuple[Candidate, str, Optional[float]]]) -> bool:
    """Posts re-alert updates. Each tuple is (candidate, kind, initial_price)."""
    if not updates:
        return False
    # Group by kind so the message header is informative
    headers: dict[str, list[tuple[Candidate, str, Optional[float]]]] = {}
    for tup in updates:
        headers.setdefault(tup[1], []).append(tup)
    summary = " · ".join(
        f"{KIND_STYLE.get(k, KIND_STYLE['new'])['header']} ({len(v)})"
        for k, v in headers.items()
    )
    payload = {
        "username": "Premarket Scanner",
        "content": f"**Update — {summary}**",
        "embeds": [_embed_for(c, kind=kind, initial_price=ip) for c, kind, ip in updates],
    }
    return _post(payload)


def send_text(message: str) -> bool:
    if not CONFIG.discord_webhook:
        return False
    r = requests.post(
        CONFIG.discord_webhook,
        json={"content": message},
        timeout=10,
    )
    return r.status_code in (200, 204)
