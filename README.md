# Flywheel Rotation Tracker

*MSTR vs ASST, powered by STRC & SATA, with BTC as home base.*

Live: https://bobbyfarrell20.github.io/btc-treasury-dashboard/

A free, static dashboard (GitHub Pages + GitHub Actions) for rotating between **Strategy (MSTR)** and **Strive (ASST)** over the bitcoin cycle, with **BTC** itself as home base. The window is the last year, starting from the Oct 2025 cycle top.

**Rules-based indicator, not financial advice.**

## What's on it
- **Header**: live BTC/MSTR/ASST mini-tickers with the day's % change, plus the current signal as a colored pill.
- **Current signal panel**: per-asset action, driven primarily by the preferred's peg (STRC → MSTR, SATA → ASST). Also covers mNAV rotation, the "both overheated → SELL_TO_BTC" rule, the short-window BTC dip rule, and EV/basic/diluted mNAV.
- **Valuation (who's cheap vs expensive)**: diluted mNAV, 1y percentile, a zone gauge (CHEAP / FAIR / RICH / OVERVALUED), price vs BTC NAV per share (premium/discount), a plain-words relative verdict, and a zone-history chart.
- **Key dates** (`data/events.json`): earnings, preferred record/dividend dates, FOMC, CPI, midterms, crypto bills in Congress.
- **Interactive charts** (Plotly, time sliders, linked axes, event markers):
  1. normalized performance, re-based to 0% at the first visible date whenever you zoom or slide
  2. mNAV (MSTR vs ASST)
  3. ASST/MSTR ratio, mNAV spread and average
  4. price vs BTC NAV/share
  5. STRC/SATA vs $100 par with ON/NEAR/WATCH/SELL bands, plus flywheel fuel (STRC ATM, buybacks, SATA outstanding)
- **Big moves log**: daily moves ≥ ±7% for MSTR/ASST and ≥ ±4% for BTC.
- **Odds jumps**: only prediction markets (Polymarket, Kalshi) whose odds moved ≥ 10 pts in a day or ≥ 15 pts in 7 days, with a likely-news headline. If none, it shows a one-line summary of current key odds.
- **`signals.json`**: machine-readable current signal, valuation, dip state, odds jumps and key odds.

## Configure
Edit these files to change what the dashboard tracks:
- `config.json`: all thresholds (peg, mNAV, valuation zones, dip, big moves, odds jumps), EV-snapshot inputs and the prediction markets tracked.
- `data/events.json`: add or update dates. Use `"date": null, "status": "TBD"` when a date is unknown.
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
- **Likely news for odds jumps**: Google News RSS search (top result around the jump date).
- **Prediction markets**: `gamma-api.polymarket.com`, `clob.polymarket.com/prices-history`, and `api.elections.kalshi.com/trade-api/v2`.

## Refresh
`.github/workflows/update.yml` runs `python scripts/update_data.py` on weekdays after the close, on weekends for BTC and the prediction markets, and on demand via *Run workflow*. Each run commits the data and redeploys Pages.

To run it locally: `pip install requests && python scripts/update_data.py`. Then serve the folder, e.g. `python -m http.server`.
