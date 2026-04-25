# 🦅 EdgeHawk

**Small-cap premarket momentum toolkit**

A Python toolkit for IBKR AU traders running premarket-into-the-open small cap longs. **It does not place orders.** It scans, ranks, alerts, sizes, and journals — you click the button in IBKR.

## Strategy fit

- **Sessions:** premarket (4am ET) → 10am ET only
- **Sides:** LONG and SHORT lanes (each can be toggled in `.env`)
- **Universe:** US listed, price $3–$20, float < 30M
- **Edges:**
  - LONG — news catalyst momentum (FDA, partnerships, contracts, beats, uplistings…)
  - SHORT — fresh dilution filings, parabolic extension fades, bearish news fades
- **Risk caps:** 2% account risk per trade, 25% max position size

## What it does

| Module | Purpose |
|---|---|
| `scanner` | Builds universe → quotes → filters → splits LONG / SHORT lanes → ranks → tags conviction |
| `data/edgar` | Real-time SEC 8-K / 424B5 / S-1 firehose |
| `data/prwires` | GlobeNewswire / BusinessWire / PR Newswire / Accesswire RSS |
| `data/news` | Weighted catalyst classifier (35+ rules, signed bullish/bearish scores) |
| `data/finviz` | Top gainers + losers as universe expansion |
| `data/float_data` | Cached float lookup (yfinance, 7d TTL) |
| `data/levels` | Per-candidate entry / SL / TP zones, pivots, VWAP, S/R |
| `data/orb` | Opening Range Breakout tracking and break detection |
| `data/short_interest` | SI % + days-to-cover scrape (24h cache) |
| `data/charts` | 5-min candle PNGs with VWAP/PMH/PDH/entry/stop overlays |
| `alerts/discord` | One consolidated message per scan: LONG green / SHORT red / TOP PICK gold; HIGH conviction only by default |
| `alerts/state` | Per-(symbol, side) re-alerter; price/vol/filing/ORB break |
| `journal` | SQLite trade log |
| `journal/stats` | Per-setup expectancy in R-multiples |
| `journal/review` | EOD aggregator and Discord summary post |
| `journal/ibkr_flex` | Auto-import IBKR fills via Flex Web Service |
| `sizing` | Risk-based + quarter-Kelly + daily loss limit + cooldown after 2 losses |
| `backtest` | Polygon-fed minute-bar replay of historical setups |

## Conviction tiers (the "focus" filter)

Each candidate is tagged HIGH / MEDIUM / LOW based on score + confirming signals (strong news, dilution, parabolic float rotation, extreme rvol, R:R quality, tiny float).

- **🥇 TOP PICK** — single highest-score candidate per scan, gold embed
- **HIGH** — strong setup, posted to Discord by default
- **MEDIUM** — only posted if `DISCORD_MIN_CONVICTION=medium`
- **LOW** — visible in CLI table only, never spams Discord

The `Why HIGH` field on each Discord embed lists the specific reasons, so you can verify the call instead of blindly trusting it.

## New commands

```bash
python -m src.cli scan                       # default: HIGH-only Discord
python -m src.cli scan --min-conviction medium   # widen for the day
python -m src.cli scan --no-charts           # disable chart attachments
python -m src.cli daily-review               # post EOD summary to Discord
python -m src.cli ibkr-import                # pull today's IBKR fills
python -m src.cli backtest setups.csv        # replay historical setups
```

## Risk circuit breakers (built into `size`)

- **Daily loss limit:** sizing locks once daily P&L hits `-DAILY_LOSS_LIMIT_PCT` (default 6%). Forces walk-away.
- **Consecutive loss cooldown:** after 2 losers in a row, sizing locks for `CONSECUTIVE_LOSS_COOLDOWN_MINUTES` (default 30 min).
- Override with `--bypass-circuit` if you really need to (think twice).

## Long vs Short lanes

Each scan emits candidates tagged `LONG` or `SHORT`. Discord embeds are color-coded; the title prefixes the side. **A single ticker can produce both** if conditions flip during the session (e.g. gapped on FDA news, then priced an offering 30 minutes later).

LONG lane qualifiers (any one):
- Gap ≥ 10% AND bullish news score ≥ `LONG_MIN_BULLISH_SCORE` (default 10) AND no dilution
- Gap ≥ 20% AND rvol ≥ 5x (pure technical breakout, no news required)

SHORT lane qualifiers (any one):
- Gap ≥ 15% AND fresh dilution filing (424B5/S-1/S-3/ATM/convertible)
- Gap ≥ 20% AND bearish news score ≥ `SHORT_MIN_BEARISH_SCORE` (default 15)
- Gap ≥ `SHORT_PARABOLIC_EXTENSION_PCT` (default 60%) — pure parabolic fade

Each candidate gets a `Trade plan` block in the alert:
- Entry zone (long: PMH break / short: rejection band below PMH)
- Stop (long: max(VWAP, recent low) / short: above PMH)
- TP1 / TP2 with R:R against entry midpoint, snapped to real levels (PDH, R1/R2, S1/S2, round numbers, VWAP, PDC)

To run longs only while you build short stats: set `ENABLE_SHORT_LANE=false` in `.env`.

## Setup

### Windows (double-click)

1. Install [Git](https://git-scm.com/downloads), [Python 3.12+](https://www.python.org/downloads/) (tick *"Add Python to PATH"*).
2. Clone the repo and switch to this branch.
3. Open the `edgehawk` folder, **double-click `setup.bat`**. It creates the venv, installs deps, and opens `.env` for you to fill in.
4. Each morning, double-click **`start-scanner.bat`** — it pulls the latest code, activates the venv, and runs the scan loop with Discord alerts.
5. Anytime, double-click **`stats.bat`** to see per-setup expectancy + recent trades.

### macOS / Linux

```bash
cd edgehawk
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env — at minimum set FINNHUB_API_KEY and DISCORD_WEBHOOK_URL
```

### API keys

- **Finnhub** (60 req/min, real-time US news): https://finnhub.io/register
- **Discord webhook**: server Settings → Integrations → Webhooks → New Webhook

## Daily workflow

**1. Premarket scan, alerting Discord every 60s** (run from ~4am ET / 6pm AEST):

```bash
python -m src.cli scan --loop 60
```

**2. When a setup looks good, calculate size before clicking buy:**

```bash
python -m src.cli size 4.20 3.95
# Equity: $800.00
# Entry: $4.20  Stop: $3.95  Risk/sh: $0.25
# Shares: 64   Risk: $16.00   Position: $268.80
```

**3. Log the entry in IBKR, then mirror it here with a setup tag:**

```bash
python -m src.cli enter NVNI gap_and_go 4.20 3.95 64 \
  --catalyst "FDA breakthrough designation" \
  --notes "first green day, broke PM HOD"
```

**4. Close it when you exit IBKR:**

```bash
python -m src.cli exit 17 5.10 --fees 2.50
```

**5. Every 25–50 trades, look at what's actually paying you:**

```bash
python -m src.cli stats
```

This is the whole point of the journal. Setups with `ExpR > 0.3` and `n >= 30` are real edges — size those up. Setups with `ExpR < 0` are *costing* you money — stop trading them no matter how exciting they feel.

## Suggested setup tags

Keep these consistent so stats don't fragment.

**Long setups:**
- `gap_and_go` — gap >10%, hold above PM high, momentum continuation
- `first_green_day` — multi-day downtrend reverses on volume
- `breakout` — break of premarket / overnight / multi-day high
- `news_runner` — fresh catalyst, no prior premarket move
- `vwap_reclaim` — loss + reclaim of VWAP into the open

**Short setups (when you start trading them):**
- `dilution_short` — fresh 424B5/ATM filing on a runner
- `parabolic_fade` — extended >60% gap, no fresh news
- `news_fade` — earnings miss / FDA reject / clinical hold gappers
- `failed_breakout` — PMH break that reclaimed below VWAP

## Risk notes

- This is not financial advice. Premarket small cap momentum is one of the riskiest intraday strategies in equities.
- The scanner **does not** detect halt risk, T1 / SSR status, or short interest squeezes — read the filings flagged on each candidate.
- The `DILUTION_RISK` flag means a 424B5 / S-1 / S-3 / FWP filing was found in the recent EDGAR feed. **Do not chase longs on these without understanding the offering.**
- Float data from yfinance is sometimes wrong by 50%+ for newly IPO'd or split-adjusted tickers. Verify on the company's latest 10-Q before sizing up.

## Roadmap

- [ ] Auto-import IBKR trade fills via Flex Web Service
- [ ] Backtest framework (Polygon.io flat files) for historical setup edge
- [ ] Halt detection (NASDAQ trader feed)
- [ ] Multi-symbol live monitoring with VWAP / HOD / LOD breach alerts
