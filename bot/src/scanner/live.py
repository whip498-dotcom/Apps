"""EdgeHawk — SQUEEZE ALERT live view (long bias).

This is the dedicated squeeze-section UI, modelled on Bullish Bob's
"Squeeze Potential / Key Levels" desk. It is intentionally LONG-ONLY:
every row in the ranking is a long candidate (gap up + RVOL + low
float + short-interest fuel + bullish key/leg/MTF context).

EdgeHawk's wider scanner methodology (longs, shorts, etc.) is
unchanged — this view just selects the long-side squeeze setups
that already pass the scanner's filters and presents them with
the squeeze-trader fields a discretionary trader actually uses:

  Key levels  : PMH, PML, PDH, PDL, ORH
  Leg levels  : L1 / L2 pullback pivots (stop reference)
  MTF         : 1m / 5m / 15m trend lights (long alignment)

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
_DOT_ON = "●"
_DOT_OFF = "○"


def _conviction_bar(confidence: int, width: int = 10) -> Text:
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


def _mtf_text(c: Candidate) -> Text:
    """3-dot MTF light: 1m · 5m · 15m. Filled = bullish (long-aligned)."""
    lv = c.quote.levels
    out = Text()
    for tf, bull in (("1", lv.mtf_1m_bull), ("5", lv.mtf_5m_bull), ("15", lv.mtf_15m_bull)):
        if bull is True:
            out.append(_DOT_ON, style="bold green")
        elif bull is False:
            out.append(_DOT_OFF, style="dim red")
        else:
            out.append(_DOT_OFF, style="dim")
    align = lv.mtf_alignment
    if align >= 3:
        suffix_style = "bold green"
    elif align == 2:
        suffix_style = "yellow"
    else:
        suffix_style = "dim"
    out.append(f" {align}/3", style=suffix_style)
    return out


def _legs_text(c: Candidate) -> Text:
    """Leg pullback pivots — stop reference for momentum longs."""
    lv = c.quote.levels
    if lv.leg1_low is None and lv.leg2_low is None:
        return Text("—", style="dim")
    out = Text()
    if lv.leg1_low is not None:
        out.append("L1 ", style="bold")
        out.append(f"${lv.leg1_low:.2f}", style="cyan")
    if lv.leg2_low is not None:
        if lv.leg1_low is not None:
            out.append(" / ", style="dim")
        out.append("L2 ", style="dim bold")
        out.append(f"${lv.leg2_low:.2f}", style="dim cyan")
    return out


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


def _long_only(candidates: list[Candidate]) -> list[Candidate]:
    """Long-bias filter for the squeeze-alert view.

    EdgeHawk's broader methodology may handle shorts elsewhere — this
    view is intentionally one-directional, mirroring Bullish Bob's
    squeeze-potential desk. Belt-and-braces guard since the scanner
    already requires a positive gap.
    """
    return [c for c in candidates if c.quote.gap_pct > 0]


def _ranking_table(candidates: list[Candidate], top: int, prev_top: set[str]) -> Table:
    table = Table(
        title="SQUEEZE ALERT — LONG BIAS · key levels · leg levels · MTF",
        title_style="bold magenta",
        caption="Bullish-Bob squeeze model · longs only · EdgeHawk wider scanner unchanged",
        caption_style="dim",
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
    table.add_column("MTF", justify="center")
    table.add_column("PMH", justify="right")
    table.add_column("PDH", justify="right")
    table.add_column("Legs (stop ref)")
    table.add_column("Trigger")
    table.add_column("Flags")

    if not candidates:
        table.add_row(*["—"] * 16)
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
            _mtf_text(c),
            pmh,
            pdh,
            _legs_text(c),
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
        ("EDGEHAWK · SQUEEZE ALERT ", "bold magenta"),
        ("(long bias) ", "bold cyan"),
        ("· ", "dim"),
        (f"scan #{iteration} ", "white"),
        (f"@ {now} ", "dim"),
        ("· ", "dim"),
        (f"{len(candidates)} longs ", "white"),
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
    return Panel(Group(line1, line2), border_style="magenta")


def watch(interval: int, top: int, console: Console) -> None:
    import time

    iteration = 0
    prev_top: set[str] = set()

    with Live(console=console, screen=False, refresh_per_second=4) as live:
        while True:
            iteration += 1
            t0 = time.monotonic()
            try:
                candidates = _long_only(scan())
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
