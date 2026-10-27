# Flywheel Rotation Tracker

*MSTR vs ASST, powered by STRC & SATA, with BTC as home base.*

Live: https://bobbyfarrell20.github.io/btc-treasury-dashboard/

A free, static dashboard (GitHub Pages + GitHub Actions) for rotating between **Strategy (MSTR)** and **Strive (ASST)** over the bitcoin cycle, with **BTC** itself as home base. The window is the last year, starting from the Oct 2025 cycle top.

**Rules-based indicator, not financial advice.**

## What's on it
Top to bottom:
- **Sticky top bar**: BTC price, the single recommendation, STRC and SATA status, a one-line summary, a data-freshness dot (green = latest close, yellow = 1 session behind, red = older), and a light/dark toggle (dark is the default; your choice is saved in the browser).
- **Flywheel status**: one big card per preferred (STRC → MSTR, SATA → ASST). Green **FLYWHEEL ON** means the peg is confirmed; yellow **NEAR PAR** means at or near $100 but not yet confirmed; red **OFF PEG** means it isn't. Each card shows what that implies, the streak, the distance to the $99.95 threshold, the parent's BTC yield, and the last 30 days of issuance.
- **Recommendation**: exactly one of **BUY MSTR**, **BUY ASST** or **WAIT** (no buy right now; data code `STAY_BTC`), with the reason, any "also valid" alternative, and when it last changed (rules below).
- **KPI strip**: prices and day %, both mNAVs, peg states, rotation z, and 10-day changes.
- **Secondary signals** (collapsible): the older per-asset actions, the dip rule and rotation.
- **Valuation**: two mNAVs per stock, each with its own 1y percentile, zone gauge (CHEAP / FAIR / RICH / OVERVALUED) and a plain-words read, plus source links for holdings, shares, debt, preferred and cash.
  - **mNAV (stock only)** = price × basic shares ÷ (BTC held × BTC price).
  - **mNAV incl. debt & preferreds** = (price × basic shares + debt at notional + preferred notional − cash/USD reserve) ÷ BTC NAV.
  - The zone-history chart defaults to EV and has a toggle. Diluted equity mNAV appears only in a tooltip and as a hidden legend line.
- **BTC yield**: change in BTC per diluted share over 30d / prior 30d / 90d / YTD / 1y. The BTC-per-share chart is shaded where each preferred's peg was confirmed ON.
- **Peg → performance**: every past confirmed-peg stretch, comparing the parent's price, BTC and BTC/share during the stretch and 7 and 30 days later. The sample is small.
- **Backtest** of the recommendation against buy-and-hold BTC, MSTR and ASST: total return, max drawdown, switches and hit rate. The call made at close t executes at close t+1, 100% in one asset, no leverage, fees ignored. There is an idle-in-cash variant too. It covers about 1 year, mostly falling, with in-sample parameters. Not predictive.
- **ATM / issuance summary**: STRC ATM proceeds and buybacks, SATA shares added (× $100 stated) and SATS, weekly and over 30d/90d/YTD/1y, plus the % of days each preferred closed at or near par.
- **Key dates** (`data/events.json`) with search, a category filter, and per-category toggles for chart annotations.
- **Charts** (Plotly, time sliders, linked axes, event markers whose hover shows the event name, a hover line with both mNAVs, peg states and the recommendation):
  1. Normalized performance, re-based to the first visible date.
  2. mNAV, defaulting to EV.
  3. ASST/MSTR ratio and mNAV spread.
  4. Price vs BTC NAV/share.
  5. STRC and SATA vs par with peg bands.
  6. Flywheel fuel: STRC ATM, buybacks, SATA issuance bars, the SATS IPO and SATA outstanding.

  Show or hide each company or BTC; the choice is saved. Every chart has a **CSV** button, and there is a full-data `data/rows.csv`.
- **Signal-change log**: every recommendation flip (reconstructed from history, plus live entries appended by the daily job to `data/signal_log.json`) with the 1d/7d/30d moves that followed.
- **Big moves**, **Odds jumps** (Polymarket/Kalshi moves ≥ 10 pts/day or ≥ 15 pts/7d with a likely-news headline), and **Method**.
- **`signals.json`**: machine-readable. Includes `recommendation`, `recommendation_reason`, `also_valid`, `signal_hash` (changes whenever the action, a peg state or an overheated flag changes), `last_changed`, `last_recommendation_change`, `flywheel` and `btc_yield`, alongside the older fields (per-asset signals, valuation, dip, odds).
- **`data/dashboard.json` → `analytics`**: full flywheel, BTC yield, backtest, peg stretches, ATM summary and signal flips.

## Configure
Edit these files to change what the dashboard tracks:
- `config.json`: `assets` (the companies tracked, each with its preferred and chart color; adding a new company also needs its own filing adapters in `scripts/`), all thresholds (peg, mNAV, valuation zones, dip, big moves, odds jumps), `mnav.signal_basis` (`"ev"` default, or `"basic"`/`"diluted"`) which picks the mNAV that drives overheated/rotation/dip/zone rules, `mnav.ev_share_basis`, preferred stated values (`capital`) and the prediction markets tracked.
- `data/events.json`: add or update dates. Use `"date": null, "status": "TBD"` when a date is unknown.
- `data/capital_events.json`: filing-sourced debt issuances/repurchases, preferred IPO sizes, and ASST debt/SATA/SATS anchors, each with a source link. Entries marked `approx` are approximations.
- `data/asst_seed.json`: hand-curated ASST holdings and shares for the period before Strive's weekly 8-K tables.

**mNAV readings shown on the page:** *Stock only* = stock market value ÷ bitcoin held (`basic` in the data). *Incl. debt & preferreds* = adds debt and preferreds, minus cash (`ev` in the data; the signal basis). The diluted-equity mNAV is reference-only, inside the collapsed card details.

## Recommendation rules
1. **Trigger**: a parent is a candidate only when its preferred's peg is confirmed ON (closes ≥ $99.95 for 3 straight days).
2. **Filter**: drop a candidate whose mNAV incl. debt & preferreds is overheated (≥ 1.25x and ≥ 90th percentile of the trailing 252 days). Peg ON but overheated means "wait / reduce size", so the call stays WAIT (no buy right now).
3. **Both survive**: if the ASST−MSTR mNAV gap z-score is unusual (≤ −1 → BUY ASST, ≥ +1 → BUY MSTR, the other listed as also valid), buy only the cheaper one. With a normal gap (or no z yet) the call is **BUY BOTH** (`BUY_BOTH`, 50/50); `lean` / `recommendation_lean` names the slightly cheaper one (z < 0 → ASST, else MSTR). The backtest holds BUY BOTH as 50% MSTR + 50% ASST.
4. Otherwise **WAIT**: no buy right now (`STAY_BTC` in signals.json; `recommendation_label` = "WAIT").

### Buy-condition flags (display only; they don't change the call)
Thresholds are in `config.json` → `buy_flags`; values are published in `signals.json` → `buy_flags.per_asset`.
- **Flywheel + Cheap** (`flywheel_cheap`): the preferred is ON and the parent's mNAV incl. debt & preferreds zone is CHEAP, or "low-FAIR" (FAIR and below the 1y median, percentile < `low_fair_max_percentile` = 0.5).
- **Very low mNAV (incl. debt & preferreds)** (`very_low_mnav`, secondary buy cue): mNAV incl. debt & preferreds ≤ `very_low_max_mnav` (1.0x) or ≤ `very_low_max_percentile` (20th percentile of 1y). It's shown even when the preferred is only NEAR PAR.

Page layout: decision banner → three chips (STRC peg, SATA peg, mNAV incl. debt & preferreds zone) → last 5 recommendation flips → preferred cards → mNAV + BTC-yield summary → charts. Rules, valuation details, peg-performance, backtest and capital tables sit further down or are collapsed.

## Signal rules (defaults)
| Rule | Default |
|---|---|
| Peg ON → BUY parent | preferred closes ≥ $99.95 for 3 straight days |
| WATCH | close < $99.90 |
| SELL / trim parent | close < $99.00, or 3+ straight down closes below $99.90 |
| Overheated | mNAV ≥ 1.25x and ≥ 90th percentile of the trailing 252 trading days |
| Both overheated | SELL_TO_BTC: sell / trim MSTR + ASST, no buy right now (overrides the peg actions) |
| Rotation | z-score of (ASST mNAV − MSTR mNAV) over 252 days: ≤ −1 favor ASST, ≥ +1 favor MSTR |
| Valuation zones | CHEAP < 1.0x · FAIR up to the 1y median · RICH above median · OVERVALUED ≥ 1.25x or ≥ 90th pct |
| Dip | BTC down ≥ 15% over the last 10 trading days → buy whichever of MSTR/ASST had the larger mNAV drop over that window (tie → larger price drop). No reliance on prior highs. |
| Odds jump | ≥ 10 pts in 1 day or ≥ 15 pts over 7 days, flagged for 7 days |

## Data sources
- **Prices**: Yahoo Finance chart API.
- **MSTR holdings and diluted shares**: [btcexposure.com CSV](https://www.btcexposure.com/data/mstr-mnav-history.csv) plus Strategy weekly 8-Ks (SEC EDGAR).
- **ASST holdings, shares, SATA, SATS and cash**: Strive SEC filings (EDGAR).
- **STRC ATM and repurchases**: Strategy weekly 8-Ks.
- **EV inputs** (`scripts/capital.py` → `data/capital_timeline.json`): each is a daily step function.
  - **Basic shares**: 10-Q/10-K cover counts, plus weekly 8-K ATM sales and buybacks between covers.
  - **Debt**: XBRL `LongTermDebt` at quarter-ends plus dated 8-K issuances and repurchases.
  - **Preferreds**: 8-K IPO sizes plus weekly ATM sales, anchored to XBRL temporary-equity shares. STRE is €100 × EURUSD.
  - **Cash**: XBRL cash, or the USD Reserve from 8-Ks since Dec 2025.
  - **Today's values**: [Strategy's KPI API](https://api.strategy.com/btc/mstrKpiData) for MSTR, and Strive's weekly 8-K tables for ASST.
  - **Residuals**: where flows between filings don't add up to the next filing, the anchor absorbs the gap. These residuals are published in `dashboard.json` (`capital_residuals`) and on the page's Method section.
- **Likely news for odds jumps**: Google News RSS search (top result around the jump date).
- **Prediction markets**: `gamma-api.polymarket.com`, `clob.polymarket.com/prices-history`, and `api.elections.kalshi.com/trade-api/v2`.

## Refresh
`.github/workflows/update.yml` runs `python scripts/update_data.py` on weekdays after the close, on weekends for BTC and the prediction markets, and on demand via *Run workflow*. Each run commits the data and redeploys Pages.

To run it locally: `pip install requests && python scripts/update_data.py`. Then serve the folder, e.g. `python -m http.server`.
