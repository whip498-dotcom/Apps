"""CLI entrypoint.

Usage examples:

  python -m src.cli scan                  # one-shot premarket scan
  python -m src.cli scan --loop 60        # rescan every 60s, alert new hits
  python -m src.cli size 4.20 3.95        # sizing for entry=$4.20 stop=$3.95
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

from .alerts.discord import send_candidates, send_text
from .config import CONFIG
from .journal.journal import all_trades, log_entry, log_exit, open_trades, trade_pnl
from .journal.stats import compute_stats, overall_stats
from .scanner.scanner import Candidate, scan, scan_summary
from .sizing.sizing import size_trade

console = Console()


def _print_candidates(cs: list[Candidate]) -> None:
    if not cs:
        console.print("[yellow]No candidates passed filters.[/yellow]")
        return
    table = Table(title=f"Premarket candidates ({len(cs)})")
    for col in ("Symbol", "Price", "Gap%", "RVol", "PM Vol", "Float", "Score", "Catalyst", "Flags"):
        table.add_column(col)
    for c in cs:
        cat = c.catalysts[0].headline[:60] if c.catalysts else ""
        table.add_row(
            c.symbol,
            f"${c.quote.last:.2f}",
            f"{c.quote.gap_pct:+.1f}",
            f"{c.quote.relative_volume:.1f}x",
            f"{c.quote.premarket_volume:,}",
            f"{c.float_shares/1_000_000:.1f}M" if c.float_shares else "?",
            f"{c.score:.1f}",
            cat,
            ",".join(c.flags),
        )
    console.print(table)


@click.group()
def cli() -> None:
    """Small-cap premarket momentum toolkit."""


@cli.command("scan")
@click.option("--loop", "loop_seconds", type=int, default=0, help="Re-scan every N seconds (0 = one shot)")
@click.option("--alert/--no-alert", default=True, help="Send Discord alerts")
@click.option("--top", default=10, help="Max candidates to show / alert")
def scan_cmd(loop_seconds: int, alert: bool, top: int) -> None:
    """Run the premarket scanner."""
    seen: set[str] = set()
    while True:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        console.rule(f"Scan @ {ts}")
        cs = scan()[:top]
        _print_candidates(cs)

        if alert:
            new = [c for c in cs if c.symbol not in seen]
            if new:
                ok = send_candidates(new, top_n=top)
                console.print(f"[cyan]Alert sent: {ok} ({len(new)} new)[/cyan]")
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
    ok = send_text("✅ Premarket scanner ping — webhook works.")
    console.print(f"Discord: {'OK' if ok else 'FAILED (check DISCORD_WEBHOOK_URL)'}")


if __name__ == "__main__":
    cli()
