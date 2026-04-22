# Paul — Full Context Profile
*Last updated: April 21, 2026. Paste this into any new Claude conversation for full context.*

## Identity & Location
- Name: Paul
- Emails: whip498@gmail.com (primary), Paul4rest@bigpond.com (coaching/secondary)
- Location: Adelaide, Australia (ACST/ACDT — UTC+9:30/+10:30)
- Partner: Holly. Has a young son.
- Day job: 7:30am–2:30pm

## Daily Schedule
- 6:00am wake-up → 7:30am–2:30pm day job → 4:00pm family → 6:00pm pre-market prep → 6:15pm study → 6:30pm US market open → 8:00pm risk check → midnight trade journal → 12:15am wind-down. Sunday: weekly review.

## Bigger Picture Goals
- Renovating the house, financial comfort for the family, building trading into a genuine income stream

---

## Trading
Discretionary day trader. US premarket small-cap momentum stocks ($1–$20, float <50M). Long-biased. Trades selectively — quality over quantity. Heavily influenced by SMB Capital methodology (Winning Day Trader, Reading the Tape, DNA of a Successful Trader), Underground Investor tandem trading, Chart Guy courses.

### TradingView Pine Scripts (7 custom indicators)
1. PMSC Pro v3.1 — main overlay. HMA cloud, L1/L2/L3 entries, pivot zones (volume-strength ranked), PMH/PML/ONH/ONL/PDH/PDL, ATR stop/T1/T2 lines, trail-to-BE, position sizing calc, A+ CONFLUENCE (4-factor: FPB-vol + quality≥7 + near strong pivot + MACD bull). Rank 1.
2. PMSC Dashboard — quality score (0-10, T/V/R/N/S breakdown), market context (SPY/QQQ vs prev close, RISK-ON/OFF), key level distances, trade P&L tracker. Rank 2.
3. PMSC FPB — SMB-style first pullback break state machine. Raw (white diamond) + vol-validated (green diamond). Session-scoped, re-arms on 3% leg extension. Rank 3.
4. PMSC MACD — 4-colour histogram, bull/bear cross, non-repainting divergence detection, TRADE/DO NOT TRADE status. Rank 4.
5. PMSC Levels — daily pivots, today's open, yesterday RTH H/L, bag holder zones (last N days' RTH highs as overhead supply). Rank 5.
6. PMSC Volume — 3-colour bars (bull/bear/spike), PM/RTH tint distinction, RVOL line, RVOL 3x+ Extreme alert. Rank 6.
7. PMSC Strategy Backtest v3.1 — standalone Pine strategy mirroring Pro logic. T1 50% partial + trail-to-BE. Force-close 10:01 AM ET. Rank 7.

36 alert conditions across 5 tiers:
- T1 Critical (4): A+ Confluence, FPB Vol-Validated, Stop Hit, Target 1 Hit
- T2 Alert (7): Cloud Flip Red, PMH Break, ONH Break, Bag Holder #1 Break, Bearish Divergence, RVOL 3x+, Target 2 Hit
- T3 Ambient (8): Cloud Flip Green, Bullish Divergence, L2 Ready, Yest RTH High Break, Today Open Cross, MACD Bull Cross, Bottom Bounce Entry, Continuation Entry
- T0 Silent Visual (12): L1 Watch, First Candle New High, Exit L1/L2, Wick Rejection, Overextended, PDH cross, PML/ONL Break, FPB Raw, Yest RTH Low Break, MACD Bear Cross
- Muted (5): Volume Spike, RVOL 2x+, PDL cross, MACD Above/Below Zero

### Python Trading App (~31,800 LOC, 99 modules)
- PySide6 desktop app with IBKR integration (live L2/tape data)
- SetupEvaluator mirroring Pine logic in Python
- Claude AI analysis on live setups (Anthropic API)
- Benzinga API for news/catalyst
- Polygon.io for market data
- JSONL accuracy logging with auto-outcome grading (EVT_STOP_HIT / T1_HIT / T2_HIT)
- Feature pipeline: 19 fields, 10 populated, 9 still at 0% fill (rvol, pct_from_vwap, atr_pct, spy/qqq/vix, premarket_range, short_interest, catalyst_age)
- 45% grading coverage (1,092/2,428 captures)
- 167 tests passing
- Valued at $200k–$300k replacement cost; $500k–$1.5M with trained model + dataset
- Known blockers: global_accuracy.json corrupt (feeds Claude self-learning loop), 9 null feature fields, realistic backtester brief written but not built

### 24/7 GitHub Actions Scanner
- Scans full US market via Polygon API for squeeze setups on small-caps
- Posts to Discord channels (confidence ≥8 for #trade-ideas)
- Runs every 15 min during market hours
- Shuffled ticker processing with float caching for full-universe coverage

### PMSC Diary (Trade Journal)
- Custom Tkinter app with IBKR fill data integration
- v2 refactor complete: extracted modules (calc, data, ibkr, theme), 23 unit tests
- Lives at C:\Users\paul9\OneDrive\Desktop\PMSC_Diary\v2\

### ML Roadmap
- XGBoost classifier: ~June 2026. Features: float, change%, RVOL, confidence, VIX, time of day, classification → trended vs faded. Needs 500+ quality sessions + feature fields wired.
- Neural network: ~October 2026. 10,000+ labelled samples, embeddings over analysis_text/catalyst_summary. Layered on XGBoost.

### Other Apps Built
- React pre-market checklist (live SPY/QQQ/VIX/DOW auto-fill)
- PMSC scanner app (React)
- Four-tab Excel trading toolkit (journal, weekly tracker, pre-market checklist, trading rules)
- Personal finance app (standalone HTML on Netlify — fortnightly budgeting, transaction import, duplicate detection, AI money leak detection, PIN lock, category drill-downs)

---

## Physical Training & Nutrition

### Training (5 days/week)
- ~80kg, lost 20kg since August 2025
- Monday: Legs quad-dominant (back squat, leg press, Bulgarian split squat, RDL, leg curl, calves, hanging leg raise)
- Tuesday: Push — upper chest focus/weak point (cable fly, incline barbell/DB, flat DB, cable/seated lateral raise, OH tricep ext, pushdown)
- Wednesday: Pull — back is best feature (weighted pull-up, Pendlay row, cable row, DB row, face pull, reverse cable fly, incline curl, hammer curl)
- Thursday: Legs hamstring-dominant (RDL, lying leg curl, hack squat, walking lunge, leg extension, calves, cable crunch)
- Friday: Push/Pull shoulders & arms (DB OHP, barbell shrug, cable lateral, bent-over lateral, EZ curl, cable curl, CGBP, skull crusher, wrist curl)
- Goal: "physical freak" status

### Nutrition
- Reverse diet: Block 1 (2200cal/200p/200c/70f) → Block 2 (2500/210p/235c/75f) → Block 3 (2800/220p/290c/80f) → Block 4 (3100/230p/325c/85f)
- All macros from FSANZ Australian food database, 4-4-9 verified
- All weights RAW. Morning shakes use 200ml milk.
- Holly eats dinner together (forTwo — cooking for 2, macros = Paul's plate)
- Shopping lists account for both portions

### Catalyst PWA (catalyst_app.html)
- Single-file HTML PWA on GitHub Pages
- Two script blocks: core (training/nutrition/check-in/history) + extensions (AI coach, correlations, offline)
- Service worker: catalyst-sw.js
- localStorage keys: catalyst_checkins_v1, catalyst_training_v1

---

## Coaching
- Client: Cameron
- App: cameron_app.html + cameron-sw.js (GitHub Pages)
- 6 tabs: Check-in, Training, Nutrition, History, Analysis, Report
- Report tab sends weekly check-in to Paul4rest@bigpond.com (weight, measurements, nutrition adherence, energy, injuries, notes + auto-attached training logs)
- localStorage: cameron_checkins_v1, cameron_training_v1, cameron_reports_v1
- Training: placeholder 5-day PPL/Upper/Lower split (needs real program)
- Nutrition: empty placeholder (needs building)

---

## Working Style
- Demands precision — catches errors, pushes until exact
- Prefers direct, practical answers — no fluff
- Systematizes everything
- Builds custom tools over off-the-shelf
- Iterates relentlessly
- Clean, functional interfaces — hates clutter
- Claude Code as primary dev tool (over Cursor)

## Growth Edges (April 2026)
1. Journal → edge analysis loop: structured weekly JSONL analysis (which setups trend at which times, confidence calibration, sizing patterns)
2. Real-time Claude as live second voice during trading sessions
3. Sample volume tracking toward XGBoost readiness (June target)
4. Fix global_accuracy.json corruption (30-min job, 3x flagged)
5. Wire remaining 9 null feature fields (2-4 hour job, most logic already exists)
