"""EdgeHawk live ranking view.

Rich Live in-terminal dashboard that re-runs the squeeze scanner on a
timer and renders an in-place ranked board. Designed for the 24/7
window during a US session when Paul wants the conviction stack at a
glance without scrolling through a log of one-shot scans.

Usage:
    python -m src.cli watch                # 30s refresh, top 15
    python -m src.cli watch --interval 15 --top 20
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..config import CONFIG
from ..data import finnhub_pool
from ..scanner.scanner import Candidate, alert_worthy, scan


_BAR_FULL = "█"
_BAR_EMPTY = "░"


def _conviction_bar(confidence: int, width: int = 10) -> Text:
    """A 10-cell bar coloured to confidence tier."""
    confidence = max(0, min(10, confidence))
    if confidence >= 9:
        style = "bold green"
    elif confidence >= 7:
        style = "bold yellow"
    elif confidence >= 5:
        style = "orange3"
    else:
        style = "dim"
    filled = _BAR_FULL * confidence
    empty = _BAR_EMPTY * (width - confidence)
    return Text.assemble((filled, style), (empty, "dim"))


def _confidence_text(confidence: int) -> Text:
    if confidence >= 9:
        return Text(f"{confidence}/10", style="bold green")
    if confidence >= 7:
        return Text(f"{confidence}/10", style="bold yellow")
    if confidence >= 5:
        return Text(f"{confidence}/10", style="orange3")
    return Text(f"{confidence}/10", style="dim")


def _trigger_text(c: Candidate) -> Text:
    """Compact key-level trigger column (PMH break > near PMH > PDH break)."""
    last = c.quote.last
    lv = c.quote.levels
    if lv.above_pmh(last):
        return Text("▲ PMH BREAK", style="bold green")
    if lv.near_pmh(last):
        return Text("→ NEAR PMH", style="yellow")
    if lv.above_pdh(last):
        return Text("▲ PDH BREAK", style="green")
    return Text("—", style="dim")


def _flags_text(flags: list[str]) -> Text:
    if not flags:
        return Text("—", style="dim")
    parts = []
    for f in flags:
        if f == "SQUEEZE":
            parts.append(Text(f, style="bold magenta"))
        elif f == "PMH_BREAK":
            parts.append(Text(f, style="bold green"))
        elif f == "NEAR_PMH":
            parts.append(Text(f, style="yellow"))
        elif f == "DILUTION_RISK":
            parts.append(Text(f, style="bold red"))
        elif f == "NO_CATALYST":
            parts.append(Text(f, style="dim"))
        else:
            parts.append(Text(f))
    out = Text()
    for i, p in enumerate(parts):
        if i:
            out.append(" ")
        out.append_text(p)
    return out


def _ranking_table(candidates: list[Candidate], top: int, prev_top: set[str]) -> Table:
    table = Table(
        title="EdgeHawk — live conviction ranking",
        title_style="bold cyan",
        header_style="bold",
        expand=True,
    )
    table.add_column("#", justify="right", width=3)
    table.add_column("Conviction", width=12)
    table.add_column("Conf")
    table.add_column("Sym", style="bold")
    table.add_column("Price", justify="right")
    table.add_column("Gap%", justify="right")
    table.add_column("RVol", justify="right")
    table.add_column("Float", justify="right")
    table.add_column("SI%", justify="right")
    table.add_column("DTC", justify="right")
    table.add_column("PMH", justify="right")
    table.add_column("PDH", justify="right")
    table.add_column("Trigger")
    table.add_column("Flags")

    if not candidates:
        table.add_row(*["—"] * 14)
        return table

    for rank, c in enumerate(candidates[:top], start=1):
        si = c.short_interest
        si_pct = f"{si.short_pct_float*100:.0f}%" if si and si.short_pct_float is not None else "—"
        dtc = f"{si.days_to_cover:.1f}" if si and si.days_to_cover is not None else "—"
        pmh = f"${c.quote.levels.pmh:.2f}" if c.quote.levels.pmh is not None else "—"
        pdh = f"${c.quote.levels.pdh:.2f}" if c.quote.levels.pdh is not None else "—"
        sym_marker = "✦ " if c.symbol not in prev_top else "  "
        sym = Text(f"{sym_marker}${c.symbol}", style="bold cyan" if c.symbol not in prev_top else "bold")

        table.add_row(
            str(rank),
            _conviction_bar(c.confidence),
            _confidence_text(c.confidence),
            sym,
            f"${c.quote.last:.2f}",
            f"{c.quote.gap_pct:+.1f}",
            f"{c.quote.relative_volume:.1f}x",
            f"{c.float_shares/1_000_000:.1f}M" if c.float_shares else "—",
            si_pct,
            dtc,
            pmh,
            pdh,
            _trigger_text(c),
            _flags_text(c.flags),
        )
    return table


def _header_panel(candidates: list[Candidate], scan_secs: float, interval: int,
                  iteration: int) -> Panel:
    now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    alert_eligible = sum(1 for c in candidates if alert_worthy(c))
    keys = finnhub_pool.key_count()
    line1 = Text.assemble(
        ("EDGEHAWK ", "bold cyan"),
        ("· ", "dim"),
        (f"scan #{iteration} ", "white"),
        (f"@ {now} ", "dim"),
        ("· ", "dim"),
        (f"{len(candidates)} candidates ", "white"),
        ("· ", "dim"),
        (f"{alert_eligible} ≥ conf {CONFIG.min_confidence} ", "bold yellow"),
        ("· ", "dim"),
        (f"scan {scan_secs:.1f}s ", "dim"),
        ("· ", "dim"),
        (f"refresh {interval}s", "dim"),
    )
    line2 = Text.assemble(
        ("filters: ", "dim"),
        (f"${CONFIG.price_min:.0f}-${CONFIG.price_max:.0f} ", "white"),
        ("· ", "dim"),
        (f"float<{CONFIG.max_float/1_000_000:.0f}M ", "white"),
        ("· ", "dim"),
        (f"gap≥{CONFIG.min_gap_pct:.0f}% ", "white"),
        ("· ", "dim"),
        (f"rvol≥{CONFIG.min_relative_volume:.1f}x ", "white"),
        ("· ", "dim"),
        (f"finnhub keys: {keys}", "white" if keys else "red"),
    )
    return Panel(Group(line1, line2), border_style="cyan")


def watch(interval: int, top: int, console: Console) -> None:
    import time

    iteration = 0
    prev_top: set[str] = set()

    with Live(console=console, screen=False, refresh_per_second=4) as live:
        while True:
            iteration += 1
            t0 = time.monotonic()
            try:
                candidates = scan()
            except KeyboardInterrupt:
                raise
            except Exception as e:
                candidates = []
                console.log(f"[red]scan error:[/red] {e}")
            scan_secs = time.monotonic() - t0

            shown = candidates[:top]
            header = _header_panel(candidates, scan_secs, interval, iteration)
            ranking = _ranking_table(candidates, top, prev_top)
            live.update(Group(header, ranking))

            prev_top = {c.symbol for c in shown}

            try:
                time.sleep(interval)
            except KeyboardInterrupt:
                break
