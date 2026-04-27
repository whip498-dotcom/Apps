# EdgeHawk — Handover Document

**Owner:** Paul (Adelaide, AU) · IBKR retail account · `whip498@gmail.com`
**Repo:** https://github.com/whip498-dotcom/Apps
**Date prepared:** 2026-04-26
**Prepared for:** A fresh Claude Code Desktop session resuming this build

---

## 0. TL;DR — what's going on

Paul thinks his code is corrupted. **It isn't — it's just split across two diverged GitHub branches that need to be merged.** All work is preserved on the remote.

What got confused locally:
- `C:\Dev\Apps\bot\` — old folder name (basic build, README + launchers)
- `C:\Dev\Apps\edgehawk\` — new folder name (only `src/` and `.venv` survived a partial copy)
- `C:\Dev\Apps\EdgeHawk Pro\` — a separate working directory used for a Claude worktree

These three are the **same project** at different stages. The fix is to wipe local copies and re-clone, then reconcile the two branches into one (see Section 5).

---

## 1. The product (what is being built)

**EdgeHawk** is a Python toolkit for IBKR Australia retail traders running US small-cap premarket momentum.

Constraints:
- **Universe:** US-listed, price `$3–$20`, float `< 30M`, gap `≥ +10%`, premarket vol `≥ 50k`, RVOL `≥ 2.0x`
- **Sessions:** 04:00 ET → 10:00 ET (premarket → first 90 min of cash session)
- **Bias:** long-side squeeze setups (Bullish-Bob model) + short fade lane (overextended >+40% gap or fresh dilution)
- **Account:** $800 starting equity, 2% max risk/trade, 25% max position size
- **Does NOT place orders.** It scans, ranks, alerts, sizes, and journals. Paul clicks the buy button in IBKR.

### Capabilities (combined across both branches)

| Layer | Module path | Purpose |
|---|---|---|
| Universe | `data/universe.py` | Build candidate ticker set |
| Quotes | `data/price.py` | Premarket OHLC, gap%, RVOL |
| News | `data/news.py` | Finnhub catalyst classifier (35+ rules, signed scores) |
| Filings | `data/edgar.py` | SEC 8-K / 424B5 / S-1 firehose |
| PR wires | `data/prwires.py` | GlobeNewswire / BusinessWire / PR Newswire / Accesswire RSS |
| Finviz | `data/finviz.py` | Top gainers/losers as universe expansion |
| Float | `data/float_data.py` | yfinance lookup, 7-day TTL cache |
| Levels | `data/levels.py` | Per-candidate entry/SL/TP, pivots, VWAP, S/R |
| ORB | `data/orb.py` | Opening Range Breakout detection |
| Short Interest | `data/short_interest.py` | SI % + days-to-cover scrape, 24h cache |
| Charts | `data/charts.py` | 5-min candle PNGs with VWAP/PMH/PDH/entry/stop overlays |
| Market context | `data/market_context.py` | SPY/QQQ/IWM/VIX deltas for the briefing |
| Finnhub pool | `data/finnhub_pool.py` | Multi-key round-robin to multiply 60 req/min budget |
| Scanner | `scanner/scanner.py` | Bullish-Bob squeeze model — confidence 1–10 |
| Live view | `scanner/live.py` | `python -m src.cli watch` — in-place rich Live UI w/ Leg Levels & MTF |
| Briefing | `briefing/briefing.py` | Claude Opus 4.7 generates 3 longs + 3 shorts at 04:00/06:30/09:20 ET |
| Briefing render | `briefing/render.py` | Briefing → Discord embed payload |
| Short pool | `briefing/short_candidates.py` | Short-side sweep (long scanner is long-only) |
| Discord | `alerts/discord.py` | Consolidated message; LONG green / SHORT red / TOP PICK gold |
| Alert state | `alerts/state.py` | Per-(symbol, side) re-alerter; trading window gate |
| Journal | `journal/journal.py` | SQLite trade log |
| Stats | `journal/stats.py` | Per-setup expectancy in R-multiples |
| EOD review | `journal/review.py` | Aggregator + Discord summary post |
| IBKR import | `journal/ibkr_flex.py` | Auto-import fills via IBKR Flex Web Service |
| Sizing | `sizing/sizing.py` | Risk-based + quarter-Kelly, daily loss limit, cooldown |
| Backtest engine | `backtest/engine.py` | Polygon-fed minute-bar replay |
| Backtest storage | `backtest/storage.py` | `data_cache/backtest_history.jsonl` |
| Backtest seed | `backtest/from_alerts.py` | Build setup CSV from last 7d HIGH-conviction alerts |
| Scheduler | `scheduler.py` | Auto IBKR import + EOD review + weekly Sunday backtest |
| Dashboard server | `dashboard/server.py` | Flask browser dashboard (legacy) |
| Dashboard native | `dashboard/native.py` | Pure-Tkinter desktop window, **always-on-top**, 3s refresh |
| Dashboard state | `dashboard/state.py` | Filesystem-as-IPC: scanner writes `dashboard_state.json`, dashboard reads it |
| CLI | `cli.py` | Click-based entrypoint — `python -m src.cli ...` |

### Conviction tiers

- **🥇 TOP PICK** — single highest-score candidate per scan, gold embed; persists across the session unless a challenger beats it by `SESSION_TOP_PICK_DELTA_PCT` (default 5%)
- **HIGH** — strong setup; posted to Discord by default (default = HIGH-only)
- **MEDIUM** — only posted if `DISCORD_MIN_CONVICTION=medium`
- **LOW** — visible in CLI table only, never spams Discord

The `Why HIGH` field on each Discord embed lists the specific reasons (strong news, dilution, parabolic float rotation, extreme rvol, R:R quality, tiny float).

### Risk circuit breakers (enforced by `sizing/sizing.py`)

- **Daily loss limit:** sizing locks once daily P&L hits `-DAILY_LOSS_LIMIT_PCT` (default 6%). Forces walk-away.
- **Consecutive loss cooldown:** after 2 losers in a row, sizing locks for `CONSECUTIVE_LOSS_COOLDOWN_MINUTES` (default 30 min).
- Override with `--bypass-circuit` flag (think twice).

---

## 2. The repo — exact branch state

| Branch | Folder | What's there |
|---|---|---|
| `main` | `bot/` | Original small-cap toolkit only. Missing dashboard, briefing, backtest, scheduler. |
| `claude/stock-trading-bot-PJoNp` | `edgehawk/` | **Full build minus the briefing module.** Has dashboard (with the flicker fixes), backtest, scheduler, IBKR import, ORB, Finviz, charts, dual-lane LONG/SHORT, conviction tiers, live status tile. |
| `claude/fix-scanner-filtering-CqPG9` | `bot/` | **Branch where the briefing was added.** Has `briefing/`, `scanner/live.py`, `data/finnhub_pool.py`, `data/market_context.py`, `.github/workflows/briefing.yml`, plus a Bullish-Bob scanner rewrite and float-bias fix. **Forked from `main` BEFORE the rename**, so still uses `bot/`. |
| `claude/inspiring-panini-3b81a8` | `bot/` (current worktree) | This worktree, currently at `main` HEAD. |

### Commits unique to `claude/stock-trading-bot-PJoNp` (have, but not in fix-scanner)

```
e4742599 Dashboard: diff-based Treeview updates for candidates table
26d7d7aa Dashboard: skip widget rebuild when state content unchanged
b3a0507c Rename bot/ to edgehawk/
967e19ea Rebrand to EdgeHawk
3d1d5283 Persist backtest results, surface in dashboard + CLI
0bd03661 Drop WebView dependency — pure Tkinter dashboard
46035bf8 Standalone desktop dashboard (PyWebView native window)
dfe80d7e Overnight movers + morning brief + dedicated live-tile channel
2af7af8d Live status tile, trading window 04:00-10:00 NY default
181ff2f2 Auto-scheduler + trading window + session-wide TOP PICK
a460e560 Add conviction tiers, charts, ORB, SI, IBKR import, backtest, daily review
b03d2d55 Add LONG + SHORT lanes, PR wires, Finviz, weighted catalysts, levels
931c47ba Add stateful re-alerting for movers and new filings
```

### Commits unique to `claude/fix-scanner-filtering-CqPG9` (have, but not in PJoNp)

```
b81d5eb3 Enforce $3-$20 / float<30M on briefing shorts
93b6f426 Add EdgeHawk Daily Briefing — Claude-authored 3 longs + 3 shorts
95097dd5 Brand live view as SQUEEZE ALERT (long bias) + add Leg Levels & MTF
19644c93 Add EdgeHawk live conviction ranking (python -m src.cli watch)
d465b1f8 Rebuild scanner around Bullish-Bob squeeze model
14bbc9d3 Fix scanner alphabet bias and tighten float filter
```

### What's the same on both

The merge base is `9259f136 Merge pull request #1 from whip498-dotcom/claude/stock-trading-bot-PJoNp` — i.e. the `main` HEAD where the basic toolkit shipped.

---

## 3. The two transcripts the user is trying to merge

**Transcript A (PJoNp):** built the full EdgeHawk product up to and including:
1. Dashboard flicker fix #1 — skip rebuild when state hash unchanged (`26d7d7aa`)
2. Dashboard flicker fix #2 — diff-based Treeview updates (`e4742599`)
3. Got cut off before fix #3 (cards in-place update) when the API timed out

**Transcript B (fix-scanner):** rebuilt the scanner around Bullish-Bob's squeeze model and added:
1. Live `watch` view with Leg Levels & MTF lights
2. Daily Briefing module — Claude Opus 4.7 generates 3L/3S three times per US trading day
3. GitHub Actions workflow that fires the briefing at 04:00 / 06:30 / 09:20 ET

Both branches are real, both work in isolation, neither one has the other's improvements.

---

## 4. The merge target — what "done" looks like

A single branch (`main` or a new `claude/edgehawk-unified`) where:

- Folder is named `edgehawk/` (the rebranded name, not `bot/`)
- Scanner is the **Bullish-Bob squeeze rewrite** from fix-scanner (`d465b1f8`), **not** the older long/short lane scanner from PJoNp (because the squeeze model is the strategic direction)
- All the supporting modules from PJoNp survive: dashboard, backtest, scheduler, IBKR Flex import, ORB, Finviz, charts, conviction tiers, live status tile, sizing circuit breakers
- The briefing module (`briefing/`) and `scanner/live.py` from fix-scanner are integrated
- `data/finnhub_pool.py` + `data/market_context.py` from fix-scanner are present
- `.github/workflows/briefing.yml` is present, paths updated to `edgehawk/` if folder moved
- `.env.example` is the union of both (Finnhub multi-key support from fix-scanner + dashboard/scheduler/backtest knobs from PJoNp + `ANTHROPIC_API_KEY` for briefing)
- `requirements.txt` includes `anthropic` (for briefing) **plus** everything in PJoNp (flask, mplfinance, sqlalchemy, etc.)
- CLI exposes both branches' commands: `scan`, `watch`, `briefing`, `size`, `enter`, `exit`, `open`, `stats`, `trades`, `ping`, `dashboard`, `dashboard-app`, `daily-review`, `ibkr-import`, `backtest`, `backtest-results`
- BAT launchers from PJoNp survive: `setup.bat`, `start-scanner.bat`, `dashboard.bat`, `start-everything.bat`, `stats.bat`, `backtest-results.bat`
- (Optional, not yet done) Dashboard flicker fix #3: cards (top pick / movers / backtest) update in-place instead of `winfo_children() + destroy()` rebuild

---

## 5. The recovery procedure (what to do first)

Do these in order. Section 5.1 fixes Paul's local mess; 5.2 produces the unified branch.

### 5.1 — Restore a clean local repo

```powershell
# Save anything you actually need from C:\Dev\Apps\bot or \edgehawk:
#   .env, journal.db, watchlist.txt, data_cache/
# (these are your local config + DB + cache, NOT in git)

# Move them OUTSIDE the repo, e.g. to C:\Dev\edgehawk-backup\
New-Item -ItemType Directory -Force "C:\Dev\edgehawk-backup"
Copy-Item "C:\Dev\Apps\bot\.env" "C:\Dev\edgehawk-backup\" -ErrorAction SilentlyContinue
Copy-Item "C:\Dev\Apps\bot\journal.db" "C:\Dev\edgehawk-backup\" -ErrorAction SilentlyContinue
Copy-Item "C:\Dev\Apps\bot\watchlist.txt" "C:\Dev\edgehawk-backup\" -ErrorAction SilentlyContinue
Copy-Item "C:\Dev\Apps\bot\data_cache" "C:\Dev\edgehawk-backup\" -Recurse -ErrorAction SilentlyContinue

# Wipe the corrupted local copies (keeping the C:\Dev\Apps\.git intact)
Remove-Item "C:\Dev\Apps\bot" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item "C:\Dev\Apps\edgehawk" -Recurse -Force -ErrorAction SilentlyContinue
```

### 5.2 — Produce the unified branch (the actual rebuild)

Run from `C:\Dev\Apps`:

```bash
git fetch origin
git checkout -b claude/edgehawk-unified origin/claude/stock-trading-bot-PJoNp
```

You now have the full EdgeHawk build (folder = `edgehawk/`). Next, port the briefing + live + scanner-rewrite changes from fix-scanner. Because that branch uses `bot/`, you cannot just `git merge` — paths conflict. The correct move is:

```bash
# Cherry-pick fix-scanner's commits, then move the touched files from bot/ → edgehawk/
git cherry-pick d465b1f8 14bbc9d3 19644c93 95097dd5 93b6f426 b81d5eb3
# This will conflict on every file because of the path. Resolve by:
#   (a) accepting fix-scanner's content
#   (b) moving the file from bot/ to edgehawk/
#   (c) deleting the duplicate at bot/
```

**Alternative (simpler):** copy files manually rather than cherry-pick.

```bash
# In a temp clone of fix-scanner, grab the files that don't exist in PJoNp:
git worktree add /tmp/fix-scanner origin/claude/fix-scanner-filtering-CqPG9
cp -r /tmp/fix-scanner/bot/src/briefing                edgehawk/src/
cp    /tmp/fix-scanner/bot/src/scanner/live.py         edgehawk/src/scanner/live.py
cp    /tmp/fix-scanner/bot/src/data/finnhub_pool.py    edgehawk/src/data/finnhub_pool.py
cp    /tmp/fix-scanner/bot/src/data/market_context.py  edgehawk/src/data/market_context.py
mkdir -p .github/workflows
cp /tmp/fix-scanner/.github/workflows/briefing.yml .github/workflows/briefing.yml
# Replace `working-directory: bot` with `working-directory: edgehawk` in briefing.yml
# Update cache-dependency-path: bot/requirements.txt → edgehawk/requirements.txt
# Update upload-artifact path: bot/data_cache/... → edgehawk/data_cache/...
```

Then **carefully** reconcile:

- `edgehawk/src/scanner/scanner.py` — fix-scanner has the squeeze-model rewrite (`d465b1f8`); PJoNp has long/short lanes. Decision needed: keep squeeze rewrite (recommended) and port the LONG/SHORT lane logic on top of it, OR keep dual-lane and bolt the squeeze scoring on. **Ask Paul before doing this. The two scanners have different `Candidate` shapes.**
- `edgehawk/src/cli.py` — merge: keep all PJoNp commands, add `watch`, `briefing` from fix-scanner.
- `edgehawk/src/alerts/discord.py` — merge: PJoNp has `send_scan` / `send_morning_brief` / `update_live_tile` / `send_daily_review`; fix-scanner has `send_briefing_payload` / `send_candidates`. Both need to coexist.
- `edgehawk/.env.example` — merge: PJoNp's full set of vars + fix-scanner's `FINNHUB_API_KEY_2/3/S`, `ANTHROPIC_API_KEY`, `SCAN_MIN_SHORT_INTEREST_PCT`, `SCAN_MIN_CONFIDENCE`.
- `edgehawk/requirements.txt` — add `anthropic>=0.39.0` to PJoNp's list.
- `edgehawk/README.md` — start from PJoNp's, add a "Daily Briefing" section.

Test locally with `python -m src.cli scan --no-alert` then `python -m src.cli watch` then `python -m src.cli briefing --slot premarket --no-alert --print` before pushing. Once green:

```bash
git add edgehawk .github
git commit -m "Unify EdgeHawk: merge briefing + live + squeeze scanner from fix-scanner branch"
git push -u origin claude/edgehawk-unified
gh pr create --title "Unify EdgeHawk build" --body "..."
```

### 5.3 — Restore Paul's local data

```powershell
# After cloning the unified branch into C:\Dev\Apps\edgehawk\
Copy-Item "C:\Dev\edgehawk-backup\.env"          "C:\Dev\Apps\edgehawk\"
Copy-Item "C:\Dev\edgehawk-backup\journal.db"    "C:\Dev\Apps\edgehawk\"
Copy-Item "C:\Dev\edgehawk-backup\watchlist.txt" "C:\Dev\Apps\edgehawk\"
Copy-Item "C:\Dev\edgehawk-backup\data_cache"    "C:\Dev\Apps\edgehawk\" -Recurse
```

Then double-click `setup.bat`, `start-scanner.bat`, `dashboard.bat`. Done.

---

## 6. Where the dashboard work was cut off

In transcript A, the assistant pushed two of three planned commits to optimise dashboard flicker:

- ✅ **Commit 1 — `26d7d7aa`** — Skip rebuild when state hash unchanged. Hash excludes the `updated_at` heartbeat so the 3s tick alone doesn't trigger rebuild. File: `edgehawk/src/dashboard/native.py`.
- ✅ **Commit 2 — `e4742599`** — Diff-based Treeview updates. Replaces delete-all + insert-all with positional `tree.item(iid, values=...)`. Existing rows update without flashing.
- ❌ **Commit 3 — not done** — Cards (top pick, movers, backtest) still use `winfo_children() + destroy()` then rebuild. Largest remaining flicker source on real change events.

The conversation transcript ends with: *"test the dashboard now after pulling. If it's smooth, we're done. If you still see flicker on real changes (new top pick, score updates), I'll do commit 3."*

So commit 3 is **optional** — only needed if Paul reports lingering flicker on actual data changes (rare event after commits 1+2). When picking it up, target the three card-render functions in `edgehawk/src/dashboard/native.py`:

- `_render_top_pick`
- `_render_movers`
- `_render_backtest`

Refactor each to update labels/values on existing widgets instead of clearing and rebuilding.

---

## 7. Operating notes (so you can be useful to Paul)

### How Paul actually uses it

1. Wakes up around 6pm AEST = 4am ET. Double-clicks `start-everything.bat` (= scanner + dashboard).
2. Watches the dashboard. When a candidate looks good, runs `python -m src.cli size 4.20 3.95` in another window.
3. Places the trade in IBKR (manually). Logs it back: `python -m src.cli enter NVNI gap_and_go 4.20 3.95 64 --catalyst "FDA"`.
4. Closes in IBKR, mirrors: `python -m src.cli exit 17 5.10 --fees 2.50`.
5. Once the briefing module ships, the GitHub Actions runner will Discord-post 3 longs + 3 shorts before he wakes up.

### Things to watch out for

- **Float data from yfinance is sometimes wrong by 50%+** for newly IPO'd or split-adjusted tickers. Verify on the latest 10-Q before sizing up. The scanner does flag tiny floats (`ROTATION_PARABOLIC_THRESHOLD=5.0`) but garbage in → garbage out.
- **No halt detection.** NASDAQ trader feed integration is on the roadmap. For now, eyeball the Level 2 in IBKR before pulling the trigger.
- **DILUTION_RISK flag** = a 424B5 / S-1 / S-3 / FWP filing was found in the recent EDGAR feed. Do not chase longs on these without reading the offering.
- **Don't over-trade past 10am ET.** The trading window is hard-coded to 04:00–10:00 NY in `.env`. Outside that window the scanner ticks (state stays warm) but Discord alerts are silenced.
- **Daily loss limit auto-locks at -6% of equity.** Paul has been told to respect the lock — don't add a flag that disables it without him asking.

### What NOT to add

Paul has explicitly scoped out (do not propose):
- Auto-execution of orders (this is intentional — IBKR's button click is the discipline gate)
- Mobile app / push notifications beyond Discord
- Anything that changes the $3–$20 / `<30M` float universe (this is the strategy)
- Backtests of strategies other than what the live scanner emits — backtest is for confirming live edges, not exploring new ones

---

## 8. API keys & secrets

Paul will paste these into `.env` after `setup.bat` opens it. None of them are committed.

| Var | Required? | Source |
|---|---|---|
| `FINNHUB_API_KEY` | yes | https://finnhub.io/register (free, 60 req/min) |
| `FINNHUB_API_KEY_2/3` | optional | extra free keys for round-robin |
| `DISCORD_WEBHOOK_URL` | yes | Discord server → Integrations → Webhooks |
| `LIVE_TILE_WEBHOOK_URL` | optional | second Discord channel for the auto-edited live tile |
| `ANTHROPIC_API_KEY` | yes (briefing only) | https://console.anthropic.com/ |
| `SEC_USER_AGENT` | yes | EDGAR requires `Name email@example.com` format |
| `POLYGON_API_KEY` | optional | https://polygon.io/ — fallback quotes |
| `POLYGON_BACKTEST_KEY` | optional | for the backtest engine (5 req/min free tier) |
| `IBKR_FLEX_TOKEN` + `IBKR_FLEX_QUERY_ID` | optional | IBKR Account Management → Reports → Flex Web Service |

For the GitHub Actions briefing workflow, the same keys go in **Repo Settings → Secrets and variables → Actions**.

---

## 9. Quick test plan (smoke test after merge)

```bash
# 0. Setup
cd edgehawk
python -m venv .venv && .venv\Scripts\activate     # (bash: source .venv/bin/activate)
pip install -r requirements.txt
cp .env.example .env   # then fill in FINNHUB_API_KEY, DISCORD_WEBHOOK_URL, ANTHROPIC_API_KEY

# 1. Discord webhook works
python -m src.cli ping

# 2. One-shot scan, no alerts (just print table)
python -m src.cli scan --no-alert

# 3. Live conviction ranking (Ctrl+C to quit)
python -m src.cli watch --interval 30 --top 15

# 4. Briefing dry-run (Claude API hit, no Discord)
python -m src.cli briefing --slot premarket --no-alert --print

# 5. Sizing math
python -m src.cli size 4.20 3.95

# 6. Dashboard (separate window, scanner must be running)
python -m src.cli dashboard-app --always-on-top
```

If all six pass, the merge is good.

---

## 10. Pointers — files to read first

If you're a fresh agent, read these in order to internalise the build:

1. `edgehawk/README.md` — strategic intent
2. `edgehawk/src/config.py` — every knob
3. `edgehawk/src/scanner/scanner.py` — the heart
4. `edgehawk/src/data/levels.py` — entry/SL/TP math
5. `edgehawk/src/sizing/sizing.py` — risk gates
6. `edgehawk/src/alerts/discord.py` — what Paul actually sees
7. `edgehawk/src/dashboard/native.py` — the always-on-top desktop UI
8. `edgehawk/src/briefing/briefing.py` — Claude prompt + structured output
9. `edgehawk/src/scheduler.py` — what auto-fires inside the scan loop
10. `edgehawk/src/cli.py` — every command Paul can type

Stop here, run the smoke test, then ask Paul what's next.

---

*End of handover. If something in this document contradicts the code, the code wins — re-read the file and update this doc.*
