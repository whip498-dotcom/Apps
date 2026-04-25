"""Discord webhook alerter — two channels, one webhook URL.

(1) **Notifications** (`send_scan`): new messages in the channel that
    trigger Discord pings/sounds. Fired only for actionable events:
      - First time a HIGH-conviction candidate appears
      - TOP PICK change
      - Re-alerts (price move ≥ N%, ORB break, new filing, vol surge)
    These are batched into one consolidated message per scan cycle, with
    embeds + chart screenshots, sorted with TOP PICK at the top.

(2) **Live status tile** (`update_live_tile`): a single persistent
    message edited in-place every scan. Always-current snapshot of the
    current TOP PICK and the ranked candidate list. Edits do **not**
    trigger notification sounds — they update silently in the background.
    Pin the message in Discord and it becomes a live dashboard.

Conviction tiers:
  HIGH   → 🥇 (top pick) or 🟢/🔴 with conviction badge, posted by default
  MEDIUM → posted only if DISCORD_MIN_CONVICTION=medium
  LOW    → never posted to Discord, visible in CLI / live tile only
"""
from __future__ import annotations

import json
from typing import Iterable, Optional

import requests

from ..config import CONFIG
from ..data.charts import render_chart
from ..journal.review import log_alert
from ..scanner.scanner import Candidate

LONG_GREEN = 0x2ECC71
SHORT_RED = 0xE74C3C
TOP_PICK_GOLD = 0xF1C40F
UPDATE_BLUE = 0x3498DB
UPDATE_PURPLE = 0x9B59B6
UPDATE_YELLOW_DARK = 0xE67E22
WARN_ORANGE = 0xE67E22
GRAY = 0x95A5A6

KIND_HEADER = {
    "new":            "Premarket scan",
    "price_up":       "Price up update",
    "price_down":     "Price down update",
    "new_filing":     "New filing",
    "vol_surge":      "Volume surge",
    "orb_break_up":   "ORB break (long)",
    "orb_break_down": "ORB break (short)",
    "top_pick_new":   "🥇 NEW TOP PICK",
}

CONVICTION_RANK = {"high": 3, "medium": 2, "low": 1}


def _conviction_badge(c: Candidate) -> str:
    if c.is_top_pick: return "🥇 TOP PICK"
    if c.conviction == "high": return "🟢 HIGH conviction" if c.side == "long" else "🔴 HIGH conviction"
    if c.conviction == "medium": return "🟡 medium"
    return "⚪ low"


def _color(c: Candidate, kind: str) -> int:
    if kind == "top_pick_new": return TOP_PICK_GOLD
    if c.is_top_pick: return TOP_PICK_GOLD
    if kind == "price_up" or kind == "orb_break_up": return UPDATE_BLUE
    if kind == "price_down" or kind == "orb_break_down": return UPDATE_BLUE
    if kind == "new_filing": return UPDATE_PURPLE
    if kind == "vol_surge": return UPDATE_YELLOW_DARK
    if c.conviction == "low": return GRAY
    if c.side == "short": return SHORT_RED
    if c.has_dilution_risk: return WARN_ORANGE
    return LONG_GREEN


def _title(c: Candidate, kind: str) -> str:
    arrow = ""
    if kind == "top_pick_new": arrow = "🥇 "
    elif kind == "price_up" or kind == "orb_break_up": arrow = "↗ "
    elif kind == "price_down" or kind == "orb_break_down": arrow = "↘ "
    elif kind == "new_filing": arrow = "📄 "
    elif kind == "vol_surge":  arrow = "⚡ "
    side_tag = "LONG" if c.side == "long" else "SHORT"
    setup = f" · {c.setup}" if c.setup else ""
    badge = ""
    if kind == "top_pick_new":
        badge = " — NEW SESSION TOP PICK"
    elif c.is_top_pick:
        badge = " 🥇 TOP PICK"
    elif c.conviction == "high":
        badge = " · HIGH"
    return f"{arrow}${c.symbol} — {side_tag}{setup}{badge}"


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
    if lv is None: return None
    body = (
        f"PMH ${lv.premarket_high:.2f} · PML ${lv.premarket_low:.2f} · VWAP ${lv.vwap:.2f}\n"
        f"PDH ${lv.prior_day_high:.2f} · PDC ${lv.prior_day_close:.2f} · PDL ${lv.prior_day_low:.2f}\n"
        f"R2 ${lv.r2:.2f} · R1 ${lv.r1:.2f} · Pivot ${lv.pivot:.2f} · S1 ${lv.s1:.2f} · S2 ${lv.s2:.2f}"
    )
    return {"name": "Levels", "value": body, "inline": False}


def _conviction_field(c: Candidate) -> Optional[dict]:
    if not c.conviction_reasons:
        return None
    body = "• " + "\n• ".join(c.conviction_reasons[:5])
    return {"name": f"Why {c.conviction.upper()}", "value": body, "inline": False}


def _short_interest_field(c: Candidate) -> Optional[dict]:
    if c.short_interest_pct is None or c.short_interest_pct == 0:
        return None
    body = f"SI {c.short_interest_pct:.1f}% of float"
    if c.days_to_cover:
        body += f" · DTC {c.days_to_cover:.1f}"
    if c.short_interest_pct >= 20:
        body += "  ⚠ squeeze risk for shorts"
    return {"name": "Short interest", "value": body, "inline": False}


def _embed_for(c: Candidate, kind: str = "new", initial_price: Optional[float] = None,
               chart_filename: Optional[str] = None) -> dict:
    fields: list[dict] = [
        {"name": "Side", "value": "🟢 LONG" if c.side == "long" else "🔴 SHORT", "inline": True},
        {"name": "Conviction", "value": _conviction_badge(c), "inline": True},
        {"name": "Score", "value": f"{c.score:.1f}", "inline": True},
        {"name": "Price", "value": f"${c.quote.last:.2f}", "inline": True},
        {"name": "Gap",   "value": f"{c.quote.gap_pct:+.1f}%", "inline": True},
        {"name": "RVol",  "value": f"{c.quote.relative_volume:.1f}x", "inline": True},
        {"name": "PM Vol","value": f"{c.quote.premarket_volume:,}", "inline": True},
        {"name": "Float", "value": f"{c.float_shares/1_000_000:.1f}M" if c.float_shares else "?", "inline": True},
        {"name": "Rotation", "value": f"{c.float_rotation:.2f}x", "inline": True},
    ]

    if initial_price is not None and kind != "new":
        delta_pct = (c.quote.last - initial_price) / initial_price * 100
        fields.append({
            "name": "Since first alert",
            "value": f"${initial_price:.2f} → ${c.quote.last:.2f} ({delta_pct:+.1f}%)",
            "inline": False,
        })

    for fld_factory in (_trade_plan_field, _conviction_field, _short_interest_field):
        f = fld_factory(c)
        if f: fields.append(f)

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

    embed: dict = {
        "title": _title(c, kind),
        "color": _color(c, kind),
        "fields": fields,
        "footer": {"text": "Premarket scanner — not financial advice"},
    }
    if chart_filename:
        embed["image"] = {"url": f"attachment://{chart_filename}"}
    return embed


def _passes_min_conviction(c: Candidate) -> bool:
    needed = CONVICTION_RANK.get(CONFIG.discord_min_conviction, 3)
    have = CONVICTION_RANK.get(c.conviction, 1)
    return have >= needed


def _post_with_files(payload: dict, files: list[tuple[str, bytes]]) -> bool:
    if not CONFIG.discord_webhook:
        return False
    try:
        if files:
            data = {"payload_json": json.dumps(payload)}
            multipart = [(f"files[{i}]", (name, blob, "image/png")) for i, (name, blob) in enumerate(files)]
            r = requests.post(CONFIG.discord_webhook, data=data, files=multipart, timeout=20)
        else:
            r = requests.post(
                CONFIG.discord_webhook,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=15,
            )
        return r.status_code in (200, 204)
    except Exception:
        return False


def _chunked(items: list, size: int = 10):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _summary_header(items: list[tuple[Candidate, str, Optional[float]]]) -> str:
    posted = [c for c, _, _ in items]
    high = sum(1 for c in posted if c.conviction == "high")
    med = sum(1 for c in posted if c.conviction == "medium")
    longs = sum(1 for c in posted if c.side == "long")
    shorts = sum(1 for c in posted if c.side == "short")
    bits = []
    if high: bits.append(f"{high} HIGH")
    if med: bits.append(f"{med} medium")
    if longs: bits.append(f"{longs} LONG")
    if shorts: bits.append(f"{shorts} SHORT")
    top = next((c for c in posted if c.is_top_pick), None)
    msg = "**Premarket scan — " + " · ".join(bits) + "**"
    if top:
        msg += f"\n🥇 **TOP PICK: ${top.symbol}** ({top.side.upper()} · {top.setup}) — score {top.score:.1f}"
    return msg


def send_scan(items: list[tuple[Candidate, str, Optional[float]]],
              attach_charts: bool = True) -> bool:
    """Send a consolidated scan message.

    `items` is list of (candidate, kind, initial_price). Kind is 'new' for
    first alerts and one of price_up/price_down/new_filing/vol_surge/orb_*
    for re-alerts.

    Filters by DISCORD_MIN_CONVICTION (default HIGH). Top pick floated to top.
    """
    if not items:
        return False

    items = [it for it in items if _passes_min_conviction(it[0])]
    if not items:
        return False

    # Top pick first, then by score desc
    items.sort(key=lambda it: (not it[0].is_top_pick, -it[0].score))

    ok = True
    for batch in _chunked(items, 10):
        embeds: list[dict] = []
        files: list[tuple[str, bytes]] = []
        for c, kind, ip in batch:
            chart_name = None
            if attach_charts:
                blob = render_chart(c.symbol, c.side, c.levels)
                if blob:
                    chart_name = f"{c.symbol}_{c.side}.png"
                    files.append((chart_name, blob))
            embeds.append(_embed_for(c, kind=kind, initial_price=ip, chart_filename=chart_name))

            log_alert({
                "symbol": c.symbol, "side": c.side, "setup": c.setup,
                "score": c.score, "conviction": c.conviction,
                "is_top_pick": c.is_top_pick, "kind": kind,
                "price": c.quote.last,
                "gap_pct": c.quote.gap_pct,
                "rvol": c.quote.relative_volume,
                "float": c.float_shares,
                "rotation": c.float_rotation,
                "entry_low": c.levels.entry_low if c.levels else None,
                "entry_high": c.levels.entry_high if c.levels else None,
                "stop": c.levels.stop if c.levels else None,
                "target_1": c.levels.target_1 if c.levels else None,
                "target_2": c.levels.target_2 if c.levels else None,
                "rr_target_1": c.levels.rr_target_1 if c.levels else None,
                "catalyst_top_tag": (
                    max(c.catalysts[0].matches, key=lambda m: abs(m[1]))[0]
                    if c.catalysts and c.catalysts[0].matches else None
                ),
            })

        payload = {
            "username": "Premarket Scanner",
            "content": _summary_header(batch),
            "embeds": embeds,
        }
        ok &= _post_with_files(payload, files)
    return ok


def send_text(message: str) -> bool:
    if not CONFIG.discord_webhook:
        return False
    return _post_with_files({"content": message}, [])


# ============================================================================
# Live status tile — single message, edited in place each scan
# ============================================================================

LIVE_TILE_STATE = CONFIG.cache_dir / "live_tile_state.json"


def _read_live_msg_id() -> Optional[str]:
    if not LIVE_TILE_STATE.exists():
        return None
    try:
        return json.loads(LIVE_TILE_STATE.read_text()).get("message_id")
    except json.JSONDecodeError:
        return None


def _write_live_msg_id(msg_id: str) -> None:
    LIVE_TILE_STATE.write_text(json.dumps({"message_id": msg_id}))


def _live_top_pick_embed(top: Candidate) -> dict:
    color = TOP_PICK_GOLD
    fields = [
        {"name": "Side", "value": "🟢 LONG" if top.side == "long" else "🔴 SHORT", "inline": True},
        {"name": "Setup", "value": top.setup or "-", "inline": True},
        {"name": "Score", "value": f"{top.score:.1f}", "inline": True},
        {"name": "Price", "value": f"${top.quote.last:.2f}", "inline": True},
        {"name": "Gap", "value": f"{top.quote.gap_pct:+.1f}%", "inline": True},
        {"name": "RVol", "value": f"{top.quote.relative_volume:.1f}x", "inline": True},
    ]
    plan = _trade_plan_field(top)
    if plan:
        fields.append(plan)
    if top.conviction_reasons:
        fields.append({
            "name": f"Why {top.conviction.upper()}",
            "value": "• " + "\n• ".join(top.conviction_reasons[:5]),
            "inline": False,
        })
    return {
        "title": f"🥇 TOP PICK — ${top.symbol}",
        "color": color,
        "fields": fields,
    }


def _live_ranked_table_embed(cs: list[Candidate]) -> dict:
    rows = ["`Rank · Side · Conv · $Sym  · Score · Setup`"]
    for i, c in enumerate(cs[:10], start=1):
        side = "L" if c.side == "long" else "S"
        conv = {"high": "H", "medium": "M", "low": "L"}.get(c.conviction, "?")
        marker = "🥇" if c.is_top_pick else "  "
        rows.append(f"`{i:>2}` {marker} {side} {conv} `${c.symbol:<5}` `{c.score:>5.1f}` `{c.setup}`")
    return {
        "title": "📋 Ranked candidates",
        "color": 0x34495E,
        "description": "\n".join(rows),
    }


def update_live_tile(candidates: list[Candidate], window_status: str = "") -> bool:
    """Post or edit the persistent live status tile.

    Edits do NOT trigger notification sounds in Discord — they're meant
    as a silent always-on dashboard. Pin the message to keep it visible.
    """
    if not CONFIG.discord_webhook or not CONFIG.enable_live_tile:
        return False

    from datetime import datetime
    import pytz
    NY = pytz.timezone("America/New_York")
    now_ny = datetime.now(NY).strftime("%H:%M:%S NY")

    embeds: list[dict] = []
    if candidates:
        top = next((c for c in candidates if c.is_top_pick), candidates[0])
        embeds.append(_live_top_pick_embed(top))
        embeds.append(_live_ranked_table_embed(candidates))
    else:
        embeds.append({
            "title": "Premarket scanner — no qualifying candidates yet",
            "color": GRAY,
            "description": "Scanner is running. Tile updates each cycle.",
        })

    content = f"📊 **Live Status — last update {now_ny}**"
    if window_status:
        content += f"  ·  {window_status}"

    payload = {
        "username": "Premarket Live",
        "content": content,
        "embeds": embeds,
    }

    msg_id = _read_live_msg_id()
    if msg_id:
        url = f"{CONFIG.discord_webhook}/messages/{msg_id}"
        try:
            r = requests.patch(
                url,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            if r.status_code in (200, 204):
                return True
            # 404 means the user deleted it; create a fresh one
        except Exception:
            return False

    try:
        r = requests.post(
            CONFIG.discord_webhook + "?wait=true",
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        if r.status_code in (200, 204):
            try:
                data = r.json()
                if "id" in data:
                    _write_live_msg_id(data["id"])
            except (ValueError, KeyError):
                pass
            return True
    except Exception:
        return False
    return False


def send_daily_review(summary: dict) -> bool:
    """Send end-of-day review summary."""
    pnl = summary.get("total_pnl", 0)
    color = LONG_GREEN if pnl >= 0 else SHORT_RED
    fields = [
        {"name": "Alerts", "value": (
            f"Total: {summary['alerts_total']}\n"
            f"HIGH: {summary['alerts_by_conviction'].get('high',0)} · "
            f"MED: {summary['alerts_by_conviction'].get('medium',0)} · "
            f"LOW: {summary['alerts_by_conviction'].get('low',0)}\n"
            f"LONG: {summary['alerts_by_side'].get('long',0)} · "
            f"SHORT: {summary['alerts_by_side'].get('short',0)}"
        ), "inline": False},
        {"name": "Trades", "value": (
            f"Taken: {summary['trades_taken']} · Closed: {summary['trades_closed']} · Open: {summary['trades_open']}\n"
            f"W/L: {summary['wins']}/{summary['losses']} ({summary['win_rate']:.0%})\n"
            f"Best: ${summary['best_trade']:+.2f} · Worst: ${summary['worst_trade']:+.2f}"
        ), "inline": False},
        {"name": "P&L", "value": f"**${pnl:+.2f}**", "inline": False},
    ]
    if summary.get("top_picks"):
        body = "\n".join(
            f"{p['symbol']} {p['side'].upper()} · {p['setup']} · score {p.get('score', 0):.1f}"
            for p in summary["top_picks"][:5]
        )
        fields.append({"name": "Top picks today", "value": body, "inline": False})

    payload = {
        "username": "Premarket Scanner",
        "content": f"**📊 Daily Review — {summary['date']}**",
        "embeds": [{
            "title": f"Session summary",
            "color": color,
            "fields": fields,
        }],
    }
    return _post_with_files(payload, [])
