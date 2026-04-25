# Small-Cap Premarket Momentum Toolkit

A Python toolkit for IBKR AU traders running premarket-into-the-open small cap longs. **It does not place orders.** It scans, ranks, alerts, sizes, and journals — you click the button in IBKR.

## Strategy fit

- **Sessions:** premarket (4am ET) → 10am ET only
- **Side:** longs only
- **Universe:** US listed, price $3–$20, float < 30M
- **Edge:** news catalyst momentum (FDA, partnerships, earnings beats, contracts, etc.)
- **Risk caps:** 2% account risk per trade, 25% max position size

## What it does

| Module | Purpose |
|---|---|
| `scanner` | Builds candidate universe → fetches premarket quotes → filters → ranks |
| `data/edgar` | Real-time SEC 8-K / 424B5 / S-1 filing firehose |
| `data/news` | Finnhub company news with catalyst keyword tagging |
| `data/float_data` | Cached float lookup (yfinance, 7d TTL) |
| `alerts/discord` | Posts top candidates to your Discord webhook |
| `journal` | SQLite-backed trade log |
| `journal/stats` | Per-setup expectancy in R-multiples |
| `sizing` | Risk-based + quarter-Kelly position sizer |

## Setup

### Windows (double-click)

1. Install [Git](https://git-scm.com/downloads), [Python 3.12+](https://www.python.org/downloads/) (tick *"Add Python to PATH"*).
2. Clone the repo and switch to this branch.
3. Open the `bot` folder, **double-click `setup.bat`**. It creates the venv, installs deps, and opens `.env` for you to fill in.
4. Each morning, double-click **`start-scanner.bat`** — it pulls the latest code, activates the venv, and runs the scan loop with Discord alerts.
5. Anytime, double-click **`stats.bat`** to see per-setup expectancy + recent trades.

### macOS / Linux

```bash
cd bot
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

Keep these consistent so stats don't fragment:

- `gap_and_go` — gap >10%, hold above PM high, momentum continuation
- `first_green_day` — multi-day downtrend reverses on volume
- `breakout` — break of premarket / overnight / multi-day high
- `news_runner` — fresh catalyst, no prior premarket move
- `vwap_reclaim` — loss + reclaim of VWAP into the open
- `dilution_fade_long` — only if you trade these (usually a short setup)

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
