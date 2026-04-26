"""Rich + Discord renderers for the Daily Briefing."""
from __future__ import annotations

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .briefing import Briefing, TradeIdea


def _conviction_style(conviction: int) -> str:
    if conviction >= 9:
        return "bold green"
    if conviction >= 7:
        return "bold yellow"
    if conviction >= 5:
        return "orange3"
    return "dim"


def _ideas_table(title: str, ideas: list[TradeIdea], side_color: str) -> Table:
    table = Table(
        title=title,
        title_style=f"bold {side_color}",
        header_style="bold",
        expand=True,
    )
    table.add_column("Sym", style="bold", width=8)
    table.add_column("Conv", width=6)
    table.add_column("Thesis", ratio=2)
    table.add_column("Entry", ratio=2)
    table.add_column("Stop", ratio=1)
    table.add_column("T1 / T2", ratio=2)
    table.add_column("Time ET", ratio=2)
    table.add_column("Levels", ratio=2)
    table.add_column("Invalidation", ratio=2)

    if not ideas:
        table.add_row("—", "—", "(no ideas this slot — see watchouts)", *(["—"] * 7))
        return table

    for i in ideas:
        conv = Text(f"{i.conviction}/10", style=_conviction_style(i.conviction))
        targets = f"T1: {i.target_t1}\nT2: {i.target_t2}"
        timing = f"in: {i.entry_time_et}\nout: {i.exit_time_et}"
        table.add_row(
            f"${i.symbol}",
            conv,
            i.thesis,
            i.entry_zone,
            i.stop,
            targets,
            timing,
            i.key_levels,
            i.invalidation,
        )
    return table


def render_briefing(briefing: Briefing, console: Console) -> None:
    header = Text.assemble(
        ("EDGEHAWK · DAILY BRIEFING ", "bold magenta"),
        ("· ", "dim"),
        (f"slot={briefing.slot.upper()} ", "bold cyan"),
        ("· ", "dim"),
        (f"{briefing.generated_at_et} ", "white"),
        ("· ", "dim"),
        (f"trading day {briefing.trading_date}", "dim"),
    )

    theme = Panel(
        Group(
            Text("Market theme", style="bold"),
            Text(briefing.market_theme),
            Text(""),
            Text("Overnight summary", style="bold"),
            Text(briefing.overnight_summary),
        ),
        border_style="cyan",
        title="Context",
        title_align="left",
    )

    longs = _ideas_table(f"LONGS ({len(briefing.longs)})", briefing.longs, "green")
    shorts = _ideas_table(f"SHORTS ({len(briefing.shorts)})", briefing.shorts, "red")

    watchouts = Panel(
        Text(briefing.risks_and_watchouts or "(none flagged)"),
        border_style="yellow",
        title="Risks & watchouts",
        title_align="left",
    )

    console.print(Panel(header, border_style="magenta"))
    console.print(theme)
    console.print(longs)
    console.print(shorts)
    console.print(watchouts)


# --- Discord embed -----------------------------------------------------------

def _idea_field(i: TradeIdea) -> dict:
    body = (
        f"**Conviction:** {i.conviction}/10\n"
        f"**Thesis:** {i.thesis}\n"
        f"**Entry:** {i.entry_zone}\n"
        f"**Time ET:** in {i.entry_time_et} · out {i.exit_time_et}\n"
        f"**Stop:** {i.stop}\n"
        f"**Targets:** T1 {i.target_t1} · T2 {i.target_t2}\n"
        f"**Levels:** {i.key_levels}\n"
        f"**Invalidation:** {i.invalidation}"
    )
    return {"name": f"${i.symbol}", "value": body[:1024], "inline": False}


def briefing_to_discord_payload(briefing: Briefing) -> dict:
    """Build a Discord webhook payload (multiple embeds in one message)."""
    embeds: list[dict] = []

    embeds.append({
        "title": f"EdgeHawk Daily Briefing — {briefing.slot.upper()}",
        "description": f"**{briefing.generated_at_et}** · trading day {briefing.trading_date}",
        "color": 0xB23AEE,  # magenta
        "fields": [
            {"name": "Market theme", "value": briefing.market_theme[:1024], "inline": False},
            {"name": "Overnight summary", "value": briefing.overnight_summary[:1024], "inline": False},
        ],
        "footer": {"text": "EdgeHawk · not financial advice"},
    })

    if briefing.longs:
        embeds.append({
            "title": f"LONGS ({len(briefing.longs)})",
            "color": 0x2ECC71,
            "fields": [_idea_field(i) for i in briefing.longs],
        })
    if briefing.shorts:
        embeds.append({
            "title": f"SHORTS ({len(briefing.shorts)})",
            "color": 0xE74C3C,
            "fields": [_idea_field(i) for i in briefing.shorts],
        })

    if briefing.risks_and_watchouts:
        embeds.append({
            "title": "Risks & watchouts",
            "color": 0xF1C40F,
            "description": briefing.risks_and_watchouts[:4000],
        })

    return {"username": "EdgeHawk", "embeds": embeds[:10]}  # Discord caps at 10 embeds
