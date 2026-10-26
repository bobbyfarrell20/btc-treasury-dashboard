# Flywheel Rotation Tracker

*MSTR vs ASST, powered by STRC & SATA, with BTC as home base.*

Live: https://bobbyfarrell20.github.io/btc-treasury-dashboard/

A free, static dashboard (GitHub Pages + GitHub Actions) for rotating between **Strategy (MSTR)** and **Strive (ASST)** over the bitcoin cycle, with **BTC** itself as home base. The window is the last year, starting from the Oct 2025 cycle top.

**Rules-based indicator, not financial advice.**

## What's on it
- **Header**: live BTC/MSTR/ASST mini-tickers with the day's % change, plus the current signal as a colored pill.
- **Current signal panel**: per-asset action, driven primarily by the preferred's peg (STRC → MSTR, SATA → ASST). Also covers mNAV rotation, the "both overheated → SELL_TO_BTC" rule, the short-window BTC dip rule, and the market-standard and all-in (EV) mNAVs.
- **Valuation (who's cheap vs expensive)**: two mNAVs per stock, each with its own 1y percentile, zone gauge (CHEAP / FAIR / RICH / OVERVALUED) and plain-words read:
  - **mNAV (market standard)** = price × basic shares ÷ (BTC held × BTC price). This is the number most sites quote.
  - **mNAV all-in (EV)** = (price × basic shares + debt at notional + preferred notional − cash/USD reserve) ÷ BTC NAV. It counts every claim on the bitcoin. Same formula for both stocks; it matches Strategy's own EV definition.
  - Diluted equity mNAV (price × assumed-diluted shares) is shown only in a tooltip and as a hidden legend line.
  - There is also a relative verdict on each basis and a zone-history chart with a basis toggle.
- **Key dates** (`data/events.json`): earnings, preferred record/dividend dates, FOMC, CPI, midterms, crypto bills in Congress.
- **Interactive charts** (Plotly, time sliders, linked axes, event markers):
  1. normalized performance, re-based to 0% at the first visible date whenever you zoom or slide
  2. mNAV (MSTR vs ASST): all-in EV solid, market standard dotted, with a toggle
  3. ASST/MSTR ratio, mNAV spread and average (on the signal basis)
  4. price vs BTC NAV/share
  5. STRC/SATA vs $100 par with ON/NEAR/WATCH/SELL bands, plus flywheel fuel (STRC ATM, buybacks, SATA outstanding)
- **Big moves log**: daily moves ≥ ±7% for MSTR/ASST and ≥ ±4% for BTC.
- **Odds jumps**: only prediction markets (Polymarket, Kalshi) whose odds moved ≥ 10 pts in a day or ≥ 15 pts in 7 days, with a likely-news headline. If none, it shows a one-line summary of current key odds.
- **`signals.json`**: machine-readable current signal, valuation, dip state, odds jumps and key odds.

## Configure
Edit these files to change what the dashboard tracks:
- `config.json`: all thresholds (peg, mNAV, valuation zones, dip, big moves, odds jumps), `mnav.signal_basis` (`"ev"` default, or `"basic"`/`"diluted"`) which picks the mNAV that drives overheated/rotation/dip/zone rules, `mnav.ev_share_basis`, preferred stated values (`capital`) and the prediction markets tracked.
- `data/events.json`: add or update dates. Use `"date": null, "status": "TBD"` when a date is unknown.
- `data/capital_events.json`: filing-sourced debt issuances/repurchases, preferred IPO sizes, and ASST debt/SATA/SATS anchors, each with a source link. Entries marked `approx` are approximations.
- `data/asst_seed.json`: hand-curated ASST holdings and shares for the period before Strive's weekly 8-K tables.

## Signal rules (defaults)
| Rule | Default |
|---|---|
| Peg ON → BUY parent | preferred closes ≥ $99.95 for 3 straight days |
| WATCH | close < $99.90 |
| SELL / trim parent | close < $99.00, or 3+ straight down closes below $99.90 |
| Overheated | mNAV ≥ 1.25x and ≥ 90th percentile of the trailing 252 trading days |
| Both overheated | SELL_TO_BTC: sell MSTR + ASST, hold BTC (overrides the peg actions) |
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
