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

from .alerts.discord import (
    send_daily_review,
    send_morning_brief,
    send_scan,
    send_text,
    update_live_tile,
)
from .alerts.state import AlertTracker, in_trading_window
from .config import CONFIG
from .journal.journal import all_trades, log_entry, log_exit, open_trades, trade_pnl
from .journal.review import build_summary
from .journal.stats import compute_stats, overall_stats
from .scanner.scanner import Candidate, scan, scan_summary
from .scheduler import Scheduler
from .sizing.sizing import size_trade

console = Console()


def _print_candidates(cs: list[Candidate]) -> None:
    if not cs:
        console.print("[yellow]No candidates passed filters.[/yellow]")
        return
    table = Table(title=f"Premarket candidates ({len(cs)})")
    for col in ("⭐", "Conv", "Side", "Symbol", "Setup", "Price", "Gap%", "RVol", "Rot",
                "Float", "Entry", "Stop", "TP1", "RR1", "Score"):
        table.add_column(col)
    for c in cs:
        side_color = "green" if c.side == "long" else "red"
        conv_color = {"high": "bright_green", "medium": "yellow", "low": "white"}[c.conviction]
        if c.levels:
            entry = f"${c.levels.entry_low:.2f}-{c.levels.entry_high:.2f}"
            stop = f"${c.levels.stop:.2f}"
            tp1 = f"${c.levels.target_1:.2f}"
            rr1 = f"{c.levels.rr_target_1:.2f}"
        else:
            entry = stop = tp1 = rr1 = "-"
        table.add_row(
            "🥇" if c.is_top_pick else "",
            f"[{conv_color}]{c.conviction.upper()}[/{conv_color}]",
            f"[{side_color}]{c.side.upper()}[/{side_color}]",
            c.symbol,
            c.setup,
            f"${c.quote.last:.2f}",
            f"{c.quote.gap_pct:+.1f}",
            f"{c.quote.relative_volume:.1f}x",
            f"{c.float_rotation:.2f}x",
            f"{c.float_shares/1_000_000:.1f}M" if c.float_shares else "?",
            entry, stop, tp1, rr1,
            f"{c.score:.1f}",
        )
    console.print(table)


@click.group()
def cli() -> None:
    """Small-cap premarket momentum toolkit."""


@cli.command("scan")
@click.option("--loop", "loop_seconds", type=int, default=0, help="Re-scan every N seconds (0 = one shot)")
@click.option("--alert/--no-alert", default=True, help="Send Discord alerts")
@click.option("--top", default=10, help="Max candidates to show / alert")
@click.option("--charts/--no-charts", default=True, help="Attach chart screenshots to Discord alerts")
@click.option("--min-conviction", type=click.Choice(["high", "medium", "low"]),
              default=None, help="Override DISCORD_MIN_CONVICTION for this run")
@click.option("--ignore-window/--respect-window", default=False,
              help="Send Discord alerts even outside the trading window")
@click.option("--auto/--no-auto", default=True,
              help="Run scheduled tasks (IBKR import, daily review, backtest) inside the loop")
def scan_cmd(loop_seconds: int, alert: bool, top: int, charts: bool,
             min_conviction: str | None, ignore_window: bool, auto: bool) -> None:
    """Run the premarket scanner. One consolidated Discord message per cycle.

    Default: only HIGH-conviction candidates are sent to Discord.
    Discord alerts are gated to TRADING_WINDOW_START–TRADING_WINDOW_END (NY).
    Outside that window the scanner still ticks (to keep state warm) but stays silent.
    """
    if min_conviction:
        object.__setattr__(CONFIG, "discord_min_conviction", min_conviction)

    tracker = AlertTracker()
    scheduler = Scheduler(log=lambda s: console.print(f"[magenta]{s}[/magenta]")) if auto else None
    morning_brief_sent = False

    while True:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        within_window = in_trading_window()
        window_tag = "🟢 IN-WINDOW" if within_window else "🔘 OFF-WINDOW"
        console.rule(
            f"Scan @ {ts}  ·  {window_tag}  ·  "
            f"Discord min={CONFIG.discord_min_conviction.upper()}  ·  "
            f"window {CONFIG.trading_window_start}–{CONFIG.trading_window_end} NY"
        )

        result = scan()
        cs = result.candidates[:top]
        movers = result.movers

        # Update session top pick — leader stays put unless beaten by 5%+
        new_leader, prev = tracker.update_session_top(cs)
        _print_candidates(cs)
        if movers:
            console.print(
                f"[blue]Overnight movers in universe (top {min(2, len(movers))}):[/blue] "
                + " · ".join(f"${m.symbol} {m.gap_pct:+.1f}%" for m in movers[:2])
            )

        # Always refresh the live status tile (silent edit, no notification ping)
        window_status = "🟢 IN-WINDOW" if within_window else "🔘 OFF-WINDOW"
        update_live_tile(cs, movers=movers, window_status=window_status)

        # One-time morning brief on the first scan that has movers (and Discord is gated open)
        if alert and not morning_brief_sent and movers and (within_window or ignore_window):
            if send_morning_brief(movers):
                morning_brief_sent = True
                console.print("[bold cyan]🌅 Morning brief sent.[/bold cyan]")

        if alert and cs and (within_window or ignore_window):
            items: list[tuple[Candidate, str, float | None]] = []
            for c in cs:
                kind = tracker.classify(c)
                # Force a 'top_pick_new' alert when leadership changes
                if c is new_leader and kind in (None, "new"):
                    kind = "top_pick_new"
                if kind is None:
                    continue
                ip = tracker.initial_price(c)
                items.append((c, kind, ip))
                tracker.record(c)

            if new_leader and prev:
                console.print(
                    f"[bold yellow]🥇 NEW TOP PICK: ${new_leader.symbol} "
                    f"(was ${prev[0]} score {prev[2]:.1f} → ${new_leader.score:.1f})[/bold yellow]"
                )

            if items:
                ok = send_scan(items, attach_charts=charts)
                eligible = [it for it in items if it[0].conviction == "high"]
                console.print(
                    f"[cyan]Discord: {ok} · {len(eligible)} HIGH conviction sent · "
                    f"{len(items)} total events evaluated[/cyan]"
                )
        elif alert and cs and not within_window:
            console.print("[yellow]Outside trading window — Discord silenced (state still tracked).[/yellow]")

        # Auto-run scheduled tasks (IBKR import / daily review / backtest)
        if scheduler is not None:
            report = scheduler.tick()
            if report.ran:
                console.print(f"[magenta]Scheduler ran: {report.ran}[/magenta]")
            if report.errors:
                console.print(f"[red]Scheduler errors: {report.errors}[/red]")

        if loop_seconds <= 0:
            return
        time.sleep(loop_seconds)


@cli.command("size")
@click.argument("entry", type=float)
@click.argument("stop", type=float)
@click.option("--setup", default=None, help="Setup name — uses Kelly if 20+ trades exist")
@click.option("--equity", type=float, default=None)
@click.option("--bypass-circuit", is_flag=True, help="Override daily loss limit / cooldown lock (use sparingly)")
def size_cmd(entry: float, stop: float, setup: str | None, equity: float | None, bypass_circuit: bool) -> None:
    """Compute position size for a planned trade."""
    rec = size_trade(entry, stop, setup=setup, equity=equity, bypass_circuit_breaker=bypass_circuit)
    eq = equity if equity is not None else CONFIG.account_equity
    console.print(f"[bold]Equity:[/bold] ${eq:,.2f}")
    console.print(f"[bold]Entry:[/bold] ${entry:.2f}  [bold]Stop:[/bold] ${stop:.2f}  "
                  f"[bold]Risk/sh:[/bold] ${abs(entry - stop):.2f}")
    if rec.locked:
        console.print(f"[red bold]🛑 LOCKED — {rec.lock_reason}[/red bold]")
        console.print("[yellow]Pass --bypass-circuit to override (think twice).[/yellow]")
        return
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


@cli.command("daily-review")
@click.option("--post/--no-post", default=True, help="Post the review to Discord")
def daily_review_cmd(post: bool):
    """Aggregate today's alerts + trades and post a Discord summary."""
    summary = build_summary()
    console.print_json(data=summary)
    if post:
        ok = send_daily_review(summary)
        console.print(f"Discord: {'OK' if ok else 'FAILED'}")


@cli.command("ibkr-import")
def ibkr_import_cmd():
    """Pull today's IBKR fills via Flex Web Service into the journal."""
    from .journal.ibkr_flex import import_today
    try:
        result = import_today()
    except Exception as e:
        console.print(f"[red]Failed: {e}[/red]")
        return
    console.print(f"[green]Imported: {result}[/green]")


@cli.command("backtest")
@click.argument("setups_csv", type=click.Path(exists=True))
@click.option("--max-hold", default=120, help="Max minutes to hold per trade")
def backtest_cmd(setups_csv: str, max_hold: int):
    """Replay setups from a CSV through Polygon historical 1m bars.

    CSV columns: symbol,trade_date(YYYY-MM-DD),side(long|short),entry,stop,target_1,target_2,setup_tag,catalyst
    """
    from datetime import date as _date

    import pandas as pd

    from .backtest.engine import Setup, run_backtest, summarize
    df = pd.read_csv(setups_csv)
    setups = [
        Setup(
            symbol=row["symbol"],
            trade_date=_date.fromisoformat(str(row["trade_date"])),
            side=row["side"],
            entry=float(row["entry"]),
            stop=float(row["stop"]),
            target_1=float(row["target_1"]),
            target_2=float(row["target_2"]),
            setup_tag=str(row.get("setup_tag", "")),
            catalyst=str(row.get("catalyst", "")),
        )
        for _, row in df.iterrows()
    ]
    console.print(f"[cyan]Running {len(setups)} setups (rate-limited at 5/min)...[/cyan]")
    results = run_backtest(setups)
    stats = summarize(results)
    console.print(f"[bold]Aggregate:[/bold] n={stats.n_setups} triggered={stats.n_triggered} "
                  f"winRate={stats.win_rate:.1%} ExpR={stats.expectancy_R:+.2f} "
                  f"PF={stats.profit_factor:.2f}")
    if stats.by_setup_tag:
        table = Table(title="By setup tag")
        for col in ("Tag", "N", "Win%", "ExpR"):
            table.add_column(col)
        for tag, b in sorted(stats.by_setup_tag.items(), key=lambda kv: -kv[1]["expectancy_R"]):
            table.add_row(tag, str(b["n"]), f"{b['win_rate']:.1%}", f"{b['expectancy_R']:+.2f}")
        console.print(table)


if __name__ == "__main__":
    cli()
