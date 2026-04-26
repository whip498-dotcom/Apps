"""EdgeHawk CLI entrypoint.

Usage examples:

  python -m src.cli scan                       # one-shot squeeze scan
  python -m src.cli scan --loop 60             # rescan every 60s, alert new hits
  python -m src.cli watch                      # live conviction ranking (in-place)
  python -m src.cli watch --interval 15 --top 20
  python -m src.cli briefing                   # Claude daily briefing (auto-detect slot)
  python -m src.cli briefing --slot premarket  # force a specific slot
  python -m src.cli size 4.20 3.95             # sizing for entry=$4.20 stop=$3.95
  python -m src.cli thesis NVNI 4.20 3.95      # F8 pressure-test before entry
  python -m src.cli enter NVNI gap_and_go 4.20 3.95 200
  python -m src.cli exit 17 5.10
  python -m src.cli stats
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import click
from rich.console import Console
from rich.table import Table

from .alerts.discord import send_briefing_payload, send_candidates, send_text
from .briefing.briefing import ALL_SLOTS, auto_slot, run_briefing
from .briefing.render import briefing_to_discord_payload, render_briefing
from .config import CONFIG
from .copilot.thesis import thesis_check
from .journal.journal import all_trades, log_entry, log_exit, open_trades, trade_pnl
from .journal.stats import compute_stats, overall_stats
from .scanner.live import watch as live_watch
from .scanner.scanner import Candidate, alert_worthy, scan, scan_summary
from .sizing.sizing import size_trade

console = Console()


def _print_candidates(cs: list[Candidate]) -> None:
    if not cs:
        console.print("[yellow]No candidates passed filters.[/yellow]")
        return
    table = Table(title=f"Squeeze candidates ({len(cs)})")
    for col in ("Sym", "Conf", "Price", "Gap%", "RVol", "Float", "SI%", "DTC", "PMH", "PDH", "Catalyst", "Flags"):
        table.add_column(col)
    for c in cs:
        cat = c.catalysts[0].headline[:50] if c.catalysts else ""
        si = c.short_interest
        si_pct = f"{si.short_pct_float*100:.0f}%" if si and si.short_pct_float is not None else "?"
        dtc = f"{si.days_to_cover:.1f}" if si and si.days_to_cover is not None else "?"
        pmh = f"${c.quote.levels.pmh:.2f}" if c.quote.levels.pmh is not None else "?"
        pdh = f"${c.quote.levels.pdh:.2f}" if c.quote.levels.pdh is not None else "?"
        table.add_row(
            c.symbol,
            f"{c.confidence}/10",
            f"${c.quote.last:.2f}",
            f"{c.quote.gap_pct:+.1f}",
            f"{c.quote.relative_volume:.1f}x",
            f"{c.float_shares/1_000_000:.1f}M" if c.float_shares else "?",
            si_pct,
            dtc,
            pmh,
            pdh,
            cat,
            ",".join(c.flags),
        )
    console.print(table)


@click.group()
def cli() -> None:
    """EdgeHawk — small-cap squeeze scanner & trading toolkit."""


@cli.command("watch")
@click.option("--interval", default=30, help="Refresh interval in seconds")
@click.option("--top", default=15, help="Number of ranked rows to render")
def watch_cmd(interval: int, top: int) -> None:
    """Live conviction ranking — in-place, refreshes on a timer."""
    try:
        live_watch(interval=interval, top=top, console=console)
    except KeyboardInterrupt:
        console.print("\n[yellow]EdgeHawk watch stopped.[/yellow]")


@cli.command("briefing")
@click.option("--slot", type=click.Choice(list(ALL_SLOTS)), default=None,
              help="Briefing slot. Auto-detected from current ET time if omitted.")
@click.option("--alert/--no-alert", default=True, help="Post to Discord")
@click.option("--print/--no-print", "do_print", default=True, help="Render to terminal")
def briefing_cmd(slot, alert, do_print) -> None:
    """Generate Claude's daily briefing (3 longs + 3 shorts, levels & timing)."""
    chosen = slot or auto_slot()
    console.print(f"[cyan]Running EdgeHawk briefing — slot={chosen}[/cyan]")
    briefing = run_briefing(chosen)
    if do_print:
        render_briefing(briefing, console)
    if alert:
        ok = send_briefing_payload(briefing_to_discord_payload(briefing))
        console.print(f"[cyan]Discord briefing posted: {ok}[/cyan]")


@cli.command("scan")
@click.option("--loop", "loop_seconds", type=int, default=0, help="Re-scan every N seconds (0 = one shot)")
@click.option("--alert/--no-alert", default=True, help="Send Discord alerts")
@click.option("--top", default=10, help="Max candidates to show / alert")
def scan_cmd(loop_seconds: int, alert: bool, top: int) -> None:
    """Run the squeeze scanner once (or in a logging loop)."""
    seen: set[str] = set()
    while True:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        console.rule(f"Scan @ {ts}")
        cs = scan()[:top]
        _print_candidates(cs)

        if alert:
            # Only fire on confidence >= CONFIG.min_confidence so the
            # #trade-ideas channel doesn't get spammed with mid-tier setups.
            new = [c for c in cs if c.symbol not in seen and alert_worthy(c)]
            if new:
                ok = send_candidates(new, top_n=top)
                console.print(
                    f"[cyan]Alert sent: {ok} ({len(new)} new ≥ conf {CONFIG.min_confidence})[/cyan]"
                )
                seen.update(c.symbol for c in new)

        if loop_seconds <= 0:
            return
        time.sleep(loop_seconds)


@cli.command("size")
@click.argument("entry", type=float)
@click.argument("stop", type=float)
@click.option("--setup", default=None, help="Setup name — uses Kelly if 20+ trades exist")
@click.option("--equity", type=float, default=None)
def size_cmd(entry: float, stop: float, setup: str | None, equity: float | None) -> None:
    """Compute position size for a planned trade."""
    rec = size_trade(entry, stop, setup=setup, equity=equity)
    eq = equity if equity is not None else CONFIG.account_equity
    console.print(f"[bold]Equity:[/bold] ${eq:,.2f}")
    console.print(f"[bold]Entry:[/bold] ${entry:.2f}  [bold]Stop:[/bold] ${stop:.2f}  "
                  f"[bold]Risk/sh:[/bold] ${abs(entry - stop):.2f}")
    console.print(f"[bold]Method:[/bold] {rec.method}")
    console.print(f"[bold]Reason:[/bold] {rec.reason}")
    console.print(f"[green bold]Shares: {rec.shares}[/green bold]   "
                  f"Risk: ${rec.risk_dollars:.2f}   Position: ${rec.position_dollars:.2f}")


@cli.command("thesis")
@click.argument("symbol")
@click.argument("entry", type=float)
@click.argument("stop", type=float)
@click.option("--shares", type=int, default=None, help="Proposed share size (defaults to sizing rec)")
@click.option("--setup", default=None, help="Setup tag (e.g. gap_and_go) — pulls similar past trades")
@click.option("--catalyst", default=None, help="Free-text catalyst override")
@click.option("--no-market", is_flag=True, default=False, help="Skip SPY/QQQ/VIX fetch for speed")
def thesis_cmd(symbol, entry, stop, shares, setup, catalyst, no_market):
    """F8 pre-entry pressure-test — 4-line verdict in <2s.

    Hot path before clicking buy. Pulls live quote, key levels, news,
    short interest, market context, similar past trades, and the sizing
    module's recommendation, then asks Claude (Sonnet 4.6, adaptive
    thinking, prompt-cached system) for a GO/WAIT/SKIP verdict.
    """
    try:
        verdict, card = thesis_check(
            symbol=symbol,
            entry=entry,
            stop=stop,
            shares=shares,
            setup=setup,
            catalyst_note=catalyst,
            skip_market=no_market,
        )
    except Exception as e:
        console.print(f"[red bold]thesis check failed:[/red bold] {e}")
        raise SystemExit(1)

    color = verdict.color()
    risk = abs(entry - stop)
    console.rule(f"[{color} bold]{verdict.verdict}[/{color} bold]  "
                 f"{symbol.upper()} ${entry:.2f}/${stop:.2f}  "
                 f"q={verdict.quality_score:.1f}  RR={verdict.rr_ratio:.2f}:1  "
                 f"({verdict.latency_ms}ms)")
    console.print(f"[bold]VERDICT[/bold]  [{color} bold]{verdict.verdict}[/{color} bold]  "
                  f"quality {verdict.quality_score:.1f}/10 · RR {verdict.rr_ratio:.2f}:1 · "
                  f"risk/sh ${risk:.2f}")
    console.print(f"[green bold]WORKS  [/green bold]  {verdict.works}")
    console.print(f"[red bold]BREAKS [/red bold]  {verdict.breaks}")
    console.print(f"[cyan bold]SIZE   [/cyan bold]  {verdict.size_note}")


@cli.command("enter")
@click.argument("symbol")
@click.argument("setup")
@click.argument("entry", type=float)
@click.argument("stop", type=float)
@click.argument("shares", type=int)
@click.option("--catalyst", default=None)
@click.option("--notes", default=None)
def enter_cmd(symbol, setup, entry, stop, shares, catalyst, notes):
    """Log a new trade entry."""
    tid = log_entry(symbol, setup, entry, stop, shares, catalyst=catalyst, notes=notes)
    console.print(f"[green]Logged trade #{tid} — {symbol} {setup} {shares}@${entry}[/green]")


@cli.command("exit")
@click.argument("trade_id", type=int)
@click.argument("exit_price", type=float)
@click.option("--fees", type=float, default=0.0)
@click.option("--notes", default=None)
def exit_cmd(trade_id, exit_price, fees, notes):
    """Close a trade."""
    log_exit(trade_id, exit_price, fees=fees, notes=notes)
    console.print(f"[green]Closed trade #{trade_id} at ${exit_price}[/green]")


@cli.command("open")
def open_cmd():
    """List open trades."""
    rows = open_trades()
    if not rows:
        console.print("[yellow]No open trades.[/yellow]")
        return
    table = Table(title="Open trades")
    for col in ("ID", "Symbol", "Setup", "Entry", "Stop", "Shares", "Risk$"):
        table.add_column(col)
    for t in rows:
        risk = abs(t.entry_price - t.stop_price) * t.shares
        table.add_row(str(t.id), t.symbol, t.setup, f"${t.entry_price:.2f}",
                      f"${t.stop_price:.2f}", str(t.shares), f"${risk:.2f}")
    console.print(table)


@cli.command("stats")
def stats_cmd():
    """Per-setup expectancy + overall stats."""
    overall = overall_stats()
    console.print(f"[bold]Overall:[/bold] {overall}")
    rows = compute_stats()
    if not rows:
        console.print("[yellow]No closed trades yet.[/yellow]")
        return
    table = Table(title="By setup")
    for col in ("Setup", "N", "Win%", "AvgWinR", "AvgLossR", "ExpR", "PF", "Total$"):
        table.add_column(col)
    for s in rows:
        table.add_row(
            s.setup,
            str(s.n),
            f"{s.win_rate:.1%}",
            f"{s.avg_win_R:+.2f}",
            f"{s.avg_loss_R:+.2f}",
            f"{s.expectancy_R:+.2f}",
            f"{s.profit_factor:.2f}",
            f"${s.total_pnl:+.2f}",
        )
    console.print(table)


@cli.command("trades")
@click.option("--limit", default=20)
def trades_cmd(limit):
    """Show recent trades."""
    rows = all_trades()[:limit]
    table = Table(title=f"Last {len(rows)} trades")
    for col in ("ID", "Symbol", "Setup", "Entry", "Stop", "Exit", "Shares", "PnL$", "R"):
        table.add_column(col)
    for t in rows:
        pnl = trade_pnl(t)
        table.add_row(
            str(t.id), t.symbol, t.setup,
            f"${t.entry_price:.2f}", f"${t.stop_price:.2f}",
            f"${t.exit_price:.2f}" if t.exit_price else "open",
            str(t.shares),
            f"{pnl.pnl:+.2f}" if pnl else "-",
            f"{pnl.r_multiple:+.2f}" if pnl else "-",
        )
    console.print(table)


@cli.command("ping")
def ping_cmd():
    """Test the Discord webhook."""
    ok = send_text("✅ EdgeHawk ping — webhook works.")
    console.print(f"Discord: {'OK' if ok else 'FAILED (check DISCORD_WEBHOOK_URL)'}")


if __name__ == "__main__":
    cli()
