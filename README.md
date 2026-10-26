# BTC Treasury Rotation Dashboard: MSTR · ASST · BTC

Live: https://bobbyfarrell20.github.io/btc-treasury-dashboard/

A free, static dashboard (GitHub Pages + GitHub Actions) for rotating between **Strategy (MSTR)**, **Strive (ASST)** and **bitcoin (BTC)** itself over the BTC cycle. The window is the last year, starting from the Oct 2025 cycle top.

**Rules-based indicator, not financial advice.**

## What's on it
- **Current signal panel**: per-asset action, driven primarily by the preferred's peg (STRC → MSTR, SATA → ASST). It also covers mNAV rotation, the "both overheated → SELL_TO_BTC" rule, the BTC-dip rule (buy whichever is hit hardest), EV/basic/diluted mNAV, events in the next 7 days and the latest big move.
- **Interactive charts** (Plotly, with time sliders and linked axes):
  - normalized performance
  - raw prices
  - mNAV (MSTR vs ASST)
  - ASST/MSTR ratio, mNAV spread and average
  - price vs BTC NAV/share (price ÷ mNAV)
  - STRC/SATA vs $100 par with ON/NEAR/WATCH/SELL bands
  - flywheel fuel (STRC ATM, STRC buybacks, SATA outstanding)
  - drawdowns from the 1-year high
  - event markers on every chart
- **Key dates** (`data/events.json`): earnings, preferred record/dividend dates, FOMC, CPI, midterms, and crypto bills in Congress.
- **Prediction markets** (`data/markets.json`): Polymarket and Kalshi odds plus daily history.
- **Big moves log**: daily moves ≥ ±7% for MSTR/ASST and ≥ ±4% for BTC.
- **`signals.json`**: machine-readable current signal, for alerts and monitors.

## Configure
Edit these files to change what the dashboard tracks:
- `config.json`: all thresholds (peg, mNAV, dip, big moves), EV-snapshot inputs and the prediction markets tracked.
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
| Dip | BTC ≥ 25% below its 365-day high → buy whichever of MSTR/ASST has the deeper drawdown |

## Data sources
- **Prices**: Yahoo Finance chart API.
- **MSTR holdings and diluted shares**: [btcexposure.com CSV](https://www.btcexposure.com/data/mstr-mnav-history.csv) plus Strategy weekly 8-Ks (SEC EDGAR).
- **ASST holdings, shares, SATA, SATS and cash**: Strive SEC filings (EDGAR).
- **STRC ATM and repurchases**: Strategy weekly 8-Ks.
- **Prediction markets**: `gamma-api.polymarket.com`, `clob.polymarket.com/prices-history`, and `api.elections.kalshi.com/trade-api/v2`.

## Refresh
`.github/workflows/update.yml` runs `python scripts/update_data.py` on weekdays after the close, on weekends for BTC and the prediction markets, and on demand via *Run workflow*. Each run commits the data and redeploys Pages.

To run it locally: `pip install requests && python scripts/update_data.py`. Then serve the folder, e.g. `python -m http.server`.
