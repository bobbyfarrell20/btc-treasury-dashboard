#!/usr/bin/env python3
"""Refresh all dashboard data from free public sources and recompute signals.

Sources
- Prices (daily close): Yahoo Finance chart API (query1.finance.yahoo.com/v8/finance/chart).
- MSTR BTC holdings & Assumed Diluted Shares history: btcexposure.com MSTR mNAV CSV (built from Strategy filings),
  overridden for holdings by Strategy's weekly 8-Ks on SEC EDGAR when newer.
- MSTR STRC ATM issuance / repurchases: Strategy weekly 8-Ks (SEC EDGAR).
- ASST BTC holdings, share counts, SATA shares outstanding: Strive 8-K/10-Q/10-K filings (SEC EDGAR) +
  data/asst_seed.json for the pre-weekly-table period.
"""
import json, re, html, time, math, os, sys, datetime as dt
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CFG = json.loads((ROOT / "config.json").read_text())
UA_SEC = {"User-Agent": os.environ.get("SEC_USER_AGENT", "BtcTreasuryDashboard research contact@example.com"),
          "Accept-Encoding": "gzip, deflate"}
UA_WEB = {"User-Agent": "Mozilla/5.0"}
TICKERS = ["MSTR", "ASST", "BTC-USD", "STRC", "SATA", "SATS"]
PARSER_VERSION = 2
CIK = {"MSTR": "1050446", "ASST": "1920406"}
MONTHS = {m: i for i, m in enumerate(["January","February","March","April","May","June","July","August",
                                       "September","October","November","December"], 1)}

def log(*a): print(*a, file=sys.stderr, flush=True)

def num(s):
    s = s.replace(",", "").strip()
    if s in ("-", "—", "–", ""): return 0.0
    return float(s)

def pdate(s):
    m = re.match(r"([A-Z][a-z]+) (\d{1,2}), (\d{4})", s.strip())
    return dt.date(int(m.group(3)), MONTHS[m.group(1)], int(m.group(2))).isoformat()

# ---------------- prices ----------------
def fetch_prices():
    out = {}
    for t in TICKERS:
        for attempt in range(6):
            host = "query1" if attempt % 2 == 0 else "query2"
            url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{t}?range=2y&interval=1d&events=split"
            try:
                r = requests.get(url, headers=UA_WEB, timeout=30); r.raise_for_status()
                res = r.json()["chart"]["result"][0]; break
            except Exception as e:
                log("yahoo retry", t, e); time.sleep(3 + attempt * 3)
        else:
            if t in ("SATS",):
                log("optional ticker unavailable", t); out[t] = {}; continue
            raise RuntimeError(f"Yahoo fetch failed for {t}")
        tz = dt.timezone(dt.timedelta(seconds=res["meta"].get("gmtoffset", 0)))
        closes = res["indicators"]["quote"][0]["close"]
        ser = {}
        for ts, c in zip(res["timestamp"], closes):
            if c is None: continue
            d = dt.datetime.fromtimestamp(ts, tz).date().isoformat()
            ser[d] = round(float(c), 4)
        # use live regular market price for today's bar if present
        out[t] = ser
        log(t, len(ser), "rows, last", max(ser), ser[max(ser)])
    return out

# ---------------- EDGAR ----------------
def edgar_docs(ticker, since, cache):
    cik = CIK[ticker]
    sub = requests.get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json", headers=UA_SEC, timeout=30).json()
    r = sub["filings"]["recent"]
    docs = []
    for f, d, acc, doc, items in zip(r["form"], r["filingDate"], r["accessionNumber"], r["primaryDocument"], r["items"]):
        if f not in ("8-K",) or d < since or "8.01" not in items: continue
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}"
        if acc not in cache or cache[acc].get("v") != PARSER_VERSION:
            time.sleep(0.2)
            try:
                t = requests.get(url, headers=UA_SEC, timeout=30).text
            except Exception as e:
                log("edgar fail", url, e); continue
            t = re.sub(r"<[^>]+>", " ", t); t = html.unescape(t); t = re.sub(r"\s+", " ", t)
            cache[acc] = {"filed": d, "url": url, "v": PARSER_VERSION, "parsed": parse_doc(ticker, t)}
        docs.append(cache[acc])
    return docs

def parse_doc(ticker, t):
    p = {}
    if ticker == "ASST":
        m = re.search(r"As of ([A-Z][a-z]+ \d{1,2}, \d{4}) As of ([A-Z][a-z]+ \d{1,2}, \d{4})", t)
        if m:
            d1, d2 = pdate(m.group(1)), pdate(m.group(2))
            def row(label):
                mm = re.search(label + r"(?: \(\d\))? (?:\$ )?([0-9,.—-]+) (?:\$ )?([0-9,.—-]+)", t)
                return (num(mm.group(1)), num(mm.group(2))) if mm else None
            btc = row(r"Bitcoin held")
            eff = row(r"Effective Common Shares Outstanding")
            a, b = row(r"Class A common stock"), row(r"Class B common stock")
            sata = row(r"SATA Stock")
            fd = row(r"Assumed Fully Diluted Shares(?: Outstanding)?")
            sats = row(r"SATS Stock")
            cash = row(r"Cash and cash equivalents \(in thousands\)")
            strc_fv = row(r"Fair value of STRC Stock \(in thousands\)")
            if btc:
                ev = []
                for i, d in enumerate((d1, d2)):
                    e = {"date": d, "btc": btc[i]}
                    basic = eff[i] if eff else (a[i] + b[i] if a and b else None)
                    if basic: e["shares_basic"] = basic
                    if fd and fd[i]: e["shares"] = fd[i]; e["shares_basis"] = "assumed_fully_diluted"
                    elif basic: e["shares"] = basic; e["shares_basis"] = "effective_common"
                    if sata: e["sata"] = sata[i]
                    if sats is not None and sats: e["sats"] = sats[i]
                    if cash: e["cash_k"] = cash[i]
                    if strc_fv: e["strc_fv_k"] = strc_fv[i]
                    ev.append(e)
                p["events"] = ev
    else:  # MSTR
        atm, rep = [], []
        for seg in re.split(r"During Period ", t)[1:]:
            m = re.match(r"([A-Z][a-z]+ \d{1,2}, \d{4}) to ([A-Z][a-z]+ \d{1,2}, \d{4})", seg)
            if not m: continue
            start, end = pdate(m.group(1)), pdate(m.group(2))
            seg = seg[:2500]
            ma = re.search(r"STRC Stock (?:Variable Rate Series A Perpetual Stretch Preferred Stock )?([0-9,]+|-|—) \$ ?([0-9.,]+|-|—) \$ ?([0-9.,]+|-|—) \$ ?([0-9.,]+)", seg)
            if ma and "Shares Sold" in seg[:1500]:
                atm.append({"start": start, "end": end, "shares": num(ma.group(1)), "notional_m": num(ma.group(2)), "net_m": num(ma.group(3))})
            mr = re.search(r"STRC Stock \(1\) ([0-9,]+|-|—) \$ ?([0-9.,]+|-|—)", seg)
            if mr and "Repurchased" in seg[:600]:
                rep.append({"start": start, "end": end, "shares": num(mr.group(1)), "usd_m": num(mr.group(2))})
            mh = re.search(r"Aggregate BTC Holdings .{0,120}?Average Purchase Price \(2\) [0-9,]+ \$ [0-9.,]+ \$ [0-9,]+ ([0-9,]+) \$", seg)
            if mh: p.setdefault("holdings", []).append({"date": end, "btc": num(mh.group(1))})
        mh2 = re.search(r"As of ([A-Z][a-z]+ \d{1,2}, \d{4}), Strategy holds approximately ([0-9,]+) bitcoin", t)
        if mh2: p.setdefault("holdings", []).append({"date": pdate(mh2.group(1)), "btc": num(mh2.group(2))})
        if atm: p["strc_atm"] = atm
        if rep: p["strc_repurchase"] = rep
    return p

# ---------------- helpers ----------------
def step_lookup(events, key):
    ev = sorted([(e["date"], e[key]) for e in events if key in e])
    def f(d):
        v = None
        for ed, val in ev:
            if ed <= d: v = val
            else: break
        return v
    return f

def pct_rank(window, x):
    w = [v for v in window if v is not None]
    if not w: return None
    return sum(1 for v in w if v <= x) / len(w)

def mean_std(w):
    w = [v for v in w if v is not None]
    if len(w) < 20: return None, None
    m = sum(w) / len(w); s = math.sqrt(sum((v - m) ** 2 for v in w) / len(w))
    return m, s

def peg_states(dates, px, pc):
    """Per-day peg state for a $100-par preferred. Returns list of state strings."""
    states, above_run, below_down_run = [], 0, 0
    prev = None
    for d in dates:
        c = px.get(d)
        if c is None: states.append(None); continue
        above_run = above_run + 1 if c >= pc["on_threshold"] else 0
        if c < pc["watch_below"]:
            below_down_run = below_down_run + 1 if (prev is not None and c < prev) else 0
        else:
            below_down_run = 0
        if c < pc["sell_below"] or below_down_run >= pc["sell_consecutive_down_closes_below_peg"]:
            s = "SELL"
        elif c < pc["watch_below"]:
            s = "WATCH"
        elif above_run >= pc["on_consecutive_days"]:
            s = "ON"
        else:
            s = "NEAR"
        states.append(s); prev = c
    return states

def main():
    cache_f = DATA / "edgar_cache.json"
    cache = json.loads(cache_f.read_text()) if cache_f.exists() else {}
    prices = fetch_prices()

    # ---- MSTR holdings/shares
    mstr_hist = {}
    try:
        r = requests.get("https://www.btcexposure.com/data/mstr-mnav-history.csv", headers=UA_WEB, timeout=30); r.raise_for_status()
        lines = r.text.strip().splitlines(); hdr = lines[0].split(",")
        for ln in lines[1:]:
            row = dict(zip(hdr, ln.split(",")))
            mstr_hist[row["date"]] = {"btc": float(row["mstr_btc_holdings"]), "shares": float(row["mstr_diluted_shares"]),
                                      "shares_source": row["shares_source"], "mnav_ref": float(row["mnav_simple"])}
        (DATA / "mstr_btcexposure.json").write_text(json.dumps(mstr_hist))
    except Exception as e:
        log("btcexposure fetch failed, using cached copy", e)
        if (DATA / "mstr_btcexposure.json").exists():
            mstr_hist = json.loads((DATA / "mstr_btcexposure.json").read_text())
    mdocs = edgar_docs("MSTR", "2025-10-01", cache)
    adocs = edgar_docs("ASST", "2026-05-20", cache)
    cache_f.write_text(json.dumps(cache, indent=0))

    m_ev = [{"date": d, "btc": v["btc"], "shares": v["shares"]} for d, v in mstr_hist.items()]
    edgar_h = sorted({h["date"]: h["btc"] for doc in mdocs for h in doc["parsed"].get("holdings", [])}.items())
    last_bx = max(mstr_hist) if mstr_hist else "0000"
    # EDGAR holdings override from the first EDGAR date onward (exact company figure)
    m_btc_edgar = step_lookup([{"date": d, "btc": b} for d, b in edgar_h], "btc")
    m_btc_bx = step_lookup(m_ev, "btc"); m_sh = step_lookup(m_ev, "shares")

    seed = json.loads((DATA / "asst_seed.json").read_text())
    a_ev = list(seed["events"])
    for doc in adocs:
        for e in doc["parsed"].get("events", []):
            e = dict(e); e["src"] = doc["url"]; a_ev.append(e)
    a_ev.sort(key=lambda e: e["date"])
    a_btc = step_lookup(a_ev, "btc"); a_sh = step_lookup(a_ev, "shares")
    sata_ev = sorted({e["date"]: e["sata"] for e in a_ev + seed.get("sata_other", []) if "sata" in e}.items())

    # ---- build daily table on trading days (MSTR calendar)
    btc = prices["BTC-USD"]
    days = sorted(prices["MSTR"])
    rows = []
    for d in days:
        b = btc.get(d)
        if b is None: continue
        r = {"date": d, "btc": b}
        for t in ("MSTR", "ASST", "STRC", "SATA"): r[t] = prices[t].get(d)
        mb = m_btc_edgar(d) if (edgar_h and d >= edgar_h[0][0]) else m_btc_bx(d)
        ms = m_sh(d)
        if r["MSTR"] and mb and ms:
            r["mstr_btc"], r["mstr_shares"] = mb, ms
            r["mstr_mnav"] = round(r["MSTR"] * ms / (mb * b), 4)
            r["mstr_nav_ps"] = round(mb * b / ms, 2)
        ab, ash = a_btc(d), a_sh(d)
        if r["ASST"] and ab and ash:
            r["asst_btc"], r["asst_shares"] = ab, ash
            r["asst_mnav"] = round(r["ASST"] * ash / (ab * b), 4)
            r["asst_nav_ps"] = round(ab * b / ash, 2)
        rows.append(r)

    # ---- rolling stats
    pc, mc, dc = CFG["peg"], CFG["mnav"], CFG["dip"]
    L = mc["lookback_trading_days"]
    for i, r in enumerate(rows):
        win = rows[max(0, i - L + 1): i + 1]
        for k in ("mstr_mnav", "asst_mnav"):
            if r.get(k) is not None:
                r[k + "_pct"] = round(pct_rank([w.get(k) for w in win], r[k]), 3)
        if r.get("mstr_mnav") and r.get("asst_mnav"):
            r["mnav_spread"] = round(r["asst_mnav"] - r["mstr_mnav"], 4)
            r["mnav_avg"] = round((r["asst_mnav"] + r["mstr_mnav"]) / 2, 4)
            m, s = mean_std([w.get("mnav_spread") for w in win[:-1]] + [r["mnav_spread"]])
            if s: r["spread_z"] = round((r["mnav_spread"] - m) / s, 3)
        if r["MSTR"] and r["ASST"]: r["ratio"] = round(r["ASST"] / r["MSTR"], 5)
        # drawdowns from rolling high (calendar days)
        cutoff = (dt.date.fromisoformat(r["date"]) - dt.timedelta(days=dc["rolling_high_days"])).isoformat()
        for t, key in (("MSTR", "MSTR"), ("ASST", "ASST")):
            if r[key] is None: continue
            hi = max(v for d, v in prices[t].items() if cutoff < d <= r["date"])
            r["dd_" + t] = round(r[key] / hi - 1, 4)
        hi = max(v for d, v in btc.items() if cutoff < d <= r["date"])
        r["dd_BTC"] = round(r["btc"] / hi - 1, 4)

    dates = [r["date"] for r in rows]
    for pref in ("STRC", "SATA"):
        st = peg_states(dates, prices[pref], pc)
        for r, s in zip(rows, st): r["peg_" + pref] = s

    # ---- signals per day
    def day_signal(r):
        out = {}
        oh = {}
        for a in ("mstr", "asst"):
            v, p = r.get(a + "_mnav"), r.get(a + "_mnav_pct")
            oh[a] = bool(v is not None and p is not None and p >= mc["overheated_percentile"] and v >= mc["overheated_min_mnav"])
        both_oh = oh["mstr"] and oh["asst"]
        z = r.get("spread_z")
        rot = "NEUTRAL"
        if z is not None and z <= mc["spread_z_favor_asst"]: rot = "FAVOR_ASST"
        elif z is not None and z >= mc["spread_z_favor_mstr"]: rot = "FAVOR_MSTR"
        dip = r.get("dd_BTC") is not None and r["dd_BTC"] <= -dc["btc_drawdown_trigger"]
        hardest = None
        cands = [(r.get("dd_MSTR"), "MSTR"), (r.get("dd_ASST"), "ASST")]
        cands = [c for c in cands if c[0] is not None]
        if cands: hardest = min(cands)[1]
        per = {}
        for asset, pref in (("MSTR", "STRC"), ("ASST", "SATA")):
            ps = r.get("peg_" + pref)
            act = {"ON": "BUY", "NEAR": "HOLD", "WATCH": "WATCH", "SELL": "SELL", None: "NO_DATA"}[ps]
            if both_oh: act = CFG["mnav"].get("both_overheated_action", "SELL_TO_BTC")
            per[asset] = {"peg_state": ps, "action": act, "mnav": r.get(asset.lower() + "_mnav"),
                          "mnav_pct_1y": r.get(asset.lower() + "_mnav_pct"), "overheated": oh[asset.lower()],
                          "drawdown_1y_high": r.get("dd_" + asset)}
        out.update(per=per, both_overheated=both_oh, rotation=rot, dip=dip, dip_target=hardest if dip else None)
        # headline
        if both_oh: head = "BOTH OVERHEATED -> SELL_TO_BTC: sell MSTR + ASST, hold BTC"
        else:
            buys = [a for a in ("MSTR", "ASST") if per[a]["action"] == "BUY"]
            sells = [a for a in ("MSTR", "ASST") if per[a]["action"] == "SELL"]
            parts = []
            if buys:
                if len(buys) == 2 and rot != "NEUTRAL": parts.append("BUY " + ("ASST" if rot == "FAVOR_ASST" else "MSTR") + " (both pegs on; mNAV rotation tilts it)")
                else: parts.append("BUY " + " + ".join(buys) + " (peg on)")
            if sells: parts.append("SELL/TRIM " + " + ".join(sells) + " (preferred off peg & falling)")
            watch = [a for a in ("MSTR", "ASST") if per[a]["action"] == "WATCH"]
            if watch: parts.append("WATCH " + " + ".join(watch) + " (preferred lost peg)")
            if dip: parts.append(f"DIP-BUY zone: BTC {r['dd_BTC']*100:.0f}% off 1y high; hardest hit = {hardest}")
            if not parts: parts.append("No strong signal - hold cash / stay patient")
            head = "; ".join(parts)
        out["headline"] = head
        return out

    for r in rows: r["sig"] = day_signal(r)

    # ---- flywheel fuel series
    strc_atm = {}
    strc_rep = {}
    for doc in mdocs:
        for a in doc["parsed"].get("strc_atm", []): strc_atm[a["end"]] = a
        for a in doc["parsed"].get("strc_repurchase", []): strc_rep[a["end"]] = a
    fuel = {"strc_atm": sorted(strc_atm.values(), key=lambda a: a["end"]),
            "strc_repurchase": sorted(strc_rep.values(), key=lambda a: a["end"]),
            "sata_outstanding": [{"date": d, "shares": s} for d, s in sata_ev]}

    # ---- big moves (close-to-close), within window
    start = CFG["window_start"]
    big = []
    for t, key in (("MSTR", "MSTR"), ("ASST", "ASST"), ("BTC", "BTC-USD")):
        thr = CFG["big_moves"][t]; ser = sorted(prices[key].items()); prev = None
        for d, c in ser:
            if prev and d >= start:
                ch = c / prev[1] - 1
                if abs(ch) >= thr:
                    big.append({"date": d, "asset": t, "change": round(ch, 4), "close": c, "prev_close": prev[1],
                                "threshold": thr, "btc_change_same_day": None})
            prev = (d, c)
    bser = dict(sorted(prices["BTC-USD"].items()))
    bkeys = sorted(bser)
    for m in big:
        i = bkeys.index(m["date"]) if m["date"] in bser else None
        if i: m["btc_change_same_day"] = round(bser[bkeys[i]] / bser[bkeys[i - 1]] - 1, 4)
    big.sort(key=lambda m: (m["date"], m["asset"]), reverse=True)

    # ---- events
    evf = json.loads((DATA / "events.json").read_text())["events"]
    today = dt.date.today()
    horizon = (today + dt.timedelta(days=CFG["events"]["upcoming_window_days"])).isoformat()
    upcoming = [e for e in evf if e.get("date") and today.isoformat() <= e["date"] <= horizon]

    # ---- EV / basic snapshot (current only)
    last = rows[-1]; b = last["btc"]
    ev = {}
    mi = CFG["ev_snapshot"]["MSTR"]
    if last.get("MSTR") and last.get("mstr_btc"):
        nav = last["mstr_btc"] * b
        mc_basic = last["MSTR"] * mi["basic_shares"]
        evv = mc_basic + (mi["debt_m"] + mi["preferred_notional_m"] - mi["cash_m"]) * 1e6
        ev["MSTR"] = {"basic_equity_mnav": round(mc_basic / nav, 4), "diluted_equity_mnav": last.get("mstr_mnav"),
                      "ev_mnav": round(evv / nav, 4), "enterprise_value_m": round(evv / 1e6), "btc_nav_m": round(nav / 1e6),
                      "inputs": dict(mi, btc_held=last["mstr_btc"], price=last["MSTR"], btc_price=b)}
    ai = CFG["ev_snapshot"]["ASST"]
    la = [e for e in a_ev if "cash_k" in e]
    if last.get("ASST") and la:
        e = la[-1]; nav = e["btc"] * b
        mc_basic = last["ASST"] * e["shares_basic"]
        pref = (e.get("sata", 0) + e.get("sats", 0)) * ai["preferred_stated_value"]
        cash = (e.get("cash_k", 0) + e.get("strc_fv_k", 0)) * 1e3
        evv = mc_basic + pref + ai["debt_m"] * 1e6 - cash
        ev["ASST"] = {"basic_equity_mnav": round(mc_basic / nav, 4), "diluted_equity_mnav": last.get("asst_mnav"),
                      "ev_mnav": round(evv / nav, 4), "enterprise_value_m": round(evv / 1e6), "btc_nav_m": round(nav / 1e6),
                      "inputs": {"as_of": e["date"], "source": e.get("src"), "btc_held": e["btc"], "basic_shares": e["shares_basic"],
                                 "sata_shares": e.get("sata"), "sats_shares": e.get("sats"), "cash_k": e.get("cash_k"),
                                 "strc_fair_value_k": e.get("strc_fv_k"), "debt_m": ai["debt_m"], "price": last["ASST"], "btc_price": b}}

    # ---- prediction markets
    pm = []
    try:
        sys.path.insert(0, str(ROOT / "scripts")); import markets
        pm = markets.fetch_all(CFG["prediction_markets"])
    except Exception as e:
        log("prediction markets failed", e)
    pm_out = {"generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "events": pm}
    if not [x for x in pm if not x.get("error")] and (DATA / "markets.json").exists():
        log("prediction markets all failed; keeping previous markets.json")
    else:
        (DATA / "markets.json").write_text(json.dumps(pm_out, separators=(",", ":")))

    out_rows = [r for r in rows if r["date"] >= (dt.date.fromisoformat(start) - dt.timedelta(days=200)).isoformat()]
    gen = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    dash = {"generated_utc": gen, "config": CFG, "rows": out_rows, "fuel": fuel,
            "asst_events": a_ev, "mstr_holdings_edgar": edgar_h,
            "latest_btc_spot": {"date": max(btc), "price": btc[max(btc)]}, "big_moves": big, "ev_snapshot": ev,
            "sats": prices.get("SATS", {})}
    (DATA / "dashboard.json").write_text(json.dumps(dash, separators=(",", ":")))

    sig = {"generated_utc": gen, "as_of": last["date"], "disclaimer": "Rules-based indicator, not financial advice.",
           "headline": last["sig"]["headline"], "per_asset": last["sig"]["per"],
           "both_overheated": last["sig"]["both_overheated"], "rotation": last["sig"]["rotation"],
           "dip": {"active": last["sig"]["dip"], "target": last["sig"]["dip_target"], "btc_drawdown": last.get("dd_BTC")},
           "peg": {p: {"price": last.get(p), "state": last.get("peg_" + p),
                       "distance_from_par": round(last[p] - pc["par"], 3) if last.get(p) else None} for p in ("STRC", "SATA")},
           "values": {k: last.get(k) for k in ("MSTR", "ASST", "btc", "mstr_mnav", "asst_mnav", "mnav_avg", "mnav_spread",
                                               "spread_z", "ratio", "dd_MSTR", "dd_ASST", "dd_BTC", "mstr_nav_ps", "asst_nav_ps")},
           "valuation_snapshot": {k: {kk: vv for kk, vv in v.items() if kk != "inputs"} for k, v in ev.items()},
           "latest_big_move": big[0] if big else None,
           "big_moves_last_30d": [m for m in big if m["date"] >= (today - dt.timedelta(days=30)).isoformat()],
           "upcoming_events_7d": upcoming,
           "prediction_markets": [{"source": x["source"], "label": x.get("label"), "url": x.get("url"),
                                   "top": sorted([{"outcome": o["outcome"], "yes": o["yes"]} for o in x.get("outcomes", []) if o.get("yes") is not None],
                                                 key=lambda o: -o["yes"])[:4]} for x in pm if not x.get("error")],
           "thresholds": {k: v for k, v in CFG.items() if k != "prediction_markets"}}
    # state-change log (for monitors)
    prev = rows[-2]["sig"] if len(rows) > 1 else None
    sig["changed_since_prev_close"] = bool(prev and (prev["headline"] != last["sig"]["headline"]))
    sig["prev_headline"] = prev["headline"] if prev else None
    (ROOT / "signals.json").write_text(json.dumps(sig, indent=2))
    log("latest", json.dumps(sig, indent=1)[:3000])

if __name__ == "__main__":
    main()
