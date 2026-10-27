#!/usr/bin/env python3
"""Refresh all dashboard data from free public sources and recompute signals.

Sources
- Prices (daily close): Yahoo Finance chart API (query1.finance.yahoo.com/v8/finance/chart).
- MSTR BTC holdings & Assumed Diluted Shares history: btcexposure.com MSTR mNAV CSV (built from Strategy filings),
  overridden for holdings by Strategy's weekly 8-Ks on SEC EDGAR when newer.
- MSTR STRC ATM issuance / repurchases: Strategy weekly 8-Ks (SEC EDGAR).
- ASST BTC holdings, share counts, SATA shares outstanding: Strive 8-K/10-Q/10-K filings (SEC EDGAR) +
  data/asst_seed.json for the pre-weekly-table period.
- Capital structure for the all-in (EV) mNAV (basic shares, debt, preferred notional, cash/USD reserve):
  scripts/capital.py -> data/capital_timeline.json (10-Q/10-K covers, XBRL companyfacts, weekly 8-Ks,
  Strategy KPI API, data/capital_events.json). EUR/USD (for STRE) from Yahoo EURUSD=X.
"""
import json, re, html, time, math, os, sys, datetime as dt
from pathlib import Path
import requests
sys.path.insert(0, str(Path(__file__).resolve().parent))
import analytics as AN
PREF_OF = {"MSTR": "STRC", "ASST": "SATA"}

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CFG = json.loads((ROOT / "config.json").read_text())
UA_SEC = {"User-Agent": os.environ.get("SEC_USER_AGENT", "BtcTreasuryDashboard research contact@example.com"),
          "Accept-Encoding": "gzip, deflate"}
UA_WEB = {"User-Agent": "Mozilla/5.0"}
TICKERS = ["MSTR", "ASST", "BTC-USD", "STRC", "SATA", "SATS", "EURUSD=X"]
PARSER_VERSION = 3
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
            if t in ("SATS", "EURUSD=X"):
                log("optional ticker unavailable", t); out[t] = {}; continue
            raise RuntimeError(f"Yahoo fetch failed for {t}")
        tz = dt.timezone(dt.timedelta(seconds=res["meta"].get("gmtoffset", 0)))
        q = res["indicators"]["quote"][0]
        closes, opens = q["close"], q.get("open") or []
        ser = {}
        for i, (ts, c) in enumerate(zip(res["timestamp"], closes)):
            if c is None and t == "BTC-USD" and i + 1 < len(opens) and opens[i + 1] is not None:
                # Yahoo occasionally publishes a null daily BTC bar; a 24/7 UTC day's close == next day's open
                c = opens[i + 1]
                log("BTC-USD null close patched from next open", dt.datetime.fromtimestamp(ts, dt.timezone.utc).date(), c)
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

    # ---- capital-structure timelines for the all-in (EV) mNAV
    sys.path.insert(0, str(ROOT / "scripts")); import capital
    cap = capital.build(UA_SEC, UA_WEB, cache, a_ev, seed)
    cache_f.write_text(json.dumps(cache, indent=0))
    (DATA / "capital_timeline.json").write_text(json.dumps(cap, indent=1))
    fx_ser = sorted(prices.get("EURUSD=X", {}).items())
    def eurusd(d):
        v = None
        for fd, fv in fx_ser:
            if fd <= d: v = fv
            else: break
        return v
    mc0 = CFG["mnav"]; BASIS = mc0.get("signal_basis", "ev"); EV_SH = mc0.get("ev_share_basis", "basic")
    BASES = ("basic", "ev", "diluted")

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
            r["mstr_mnav_diluted"] = round(r["MSTR"] * ms / (mb * b), 4)
            r["mstr_nav_ps"] = round(mb * b / ms, 2)
        ab, ash = a_btc(d), a_sh(d)
        if r["ASST"] and ab and ash:
            r["asst_btc"], r["asst_shares"] = ab, ash
            r["asst_mnav_diluted"] = round(r["ASST"] * ash / (ab * b), 4)
            r["asst_nav_ps"] = round(ab * b / ash, 2)
        for A, a, bt, dil in (("MSTR", "mstr", mb, ms), ("ASST", "asst", ab, ash)):
            if not (r[A] and bt): continue
            x = capital.ev_inputs(cap, A, d, eurusd, CFG.get("capital"))
            if not x or x.get("missing_fx"): continue
            nav = bt * b
            r[a + "_basic_shares"] = x["basic_shares"]
            r[a + "_mnav_basic"] = round(r[A] * x["basic_shares"] / nav, 4)
            eq_sh = dil if (EV_SH == "diluted" and dil) else x["basic_shares"]
            evv = r[A] * eq_sh + x["debt"] + x["pref"] - x["cash"]
            r[a + "_mnav_ev"] = round(evv / nav, 4)
            r[a + "_ev_m"] = round(evv / 1e6, 1)
            r[a + "_debt_m"], r[a + "_pref_m"], r[a + "_cash_m"] = round(x["debt"] / 1e6, 1), round(x["pref"] / 1e6, 1), round(x["cash"] / 1e6, 1)
        for a in ("mstr", "asst"):
            if r.get(a + "_mnav_" + BASIS) is not None: r[a + "_mnav"] = r[a + "_mnav_" + BASIS]
        rows.append(r)

    # ---- rolling stats
    pc, mc, dc = CFG["peg"], CFG["mnav"], CFG["dip"]
    L = mc["lookback_trading_days"]
    for i, r in enumerate(rows):
        win = rows[max(0, i - L + 1): i + 1]
        for bs in BASES + ("",):
            sfx = "_" + bs if bs else ""
            for k in ("mstr_mnav" + sfx, "asst_mnav" + sfx):
                if r.get(k) is not None:
                    r[k + "_pct"] = round(pct_rank([w.get(k) for w in win], r[k]), 3)
            m_, a_ = r.get("mstr_mnav" + sfx), r.get("asst_mnav" + sfx)
            if m_ and a_:
                r["mnav_spread" + sfx] = round(a_ - m_, 4)
                r["mnav_avg" + sfx] = round((a_ + m_) / 2, 4)
                m, s = mean_std([w.get("mnav_spread" + sfx) for w in win[:-1]] + [r["mnav_spread" + sfx]])
                if s: r["spread_z" + sfx] = round((r["mnav_spread" + sfx] - m) / s, 3)
                # display-only relative-value stats (same trailing window as spread_z; the rule still uses spread_z)
                if m is not None:
                    r["spread_mean" + sfx], r["spread_sd" + sfx] = round(m, 4), round(s, 4)
                r["spread_pct" + sfx] = round(pct_rank([w.get("mnav_spread" + sfx) for w in win], r["mnav_spread" + sfx]), 3)
                if m_ > 0:
                    r["mnav_ratio" + sfx] = round(a_ / m_, 4)
                    rw = [w["asst_mnav" + sfx] / w["mstr_mnav" + sfx] for w in win[:-1]
                          if w.get("asst_mnav" + sfx) and w.get("mstr_mnav" + sfx)] + [r["mnav_ratio" + sfx]]
                    rm, rs = mean_std(rw)
                    if rm is not None:
                        r["ratio_mean" + sfx], r["ratio_sd" + sfx] = round(rm, 4), round(rs, 4)
                        srt = sorted(rw); r["ratio_median" + sfx] = round(srt[len(srt) // 2] if len(srt) % 2 else (srt[len(srt) // 2 - 1] + srt[len(srt) // 2]) / 2, 4)
                        if rs: r["ratio_z" + sfx] = round((r["mnav_ratio" + sfx] - rm) / rs, 3)
                    r["ratio_pct" + sfx] = round(pct_rank(rw, r["mnav_ratio" + sfx]), 3)
        if r["MSTR"] and r["ASST"]: r["ratio"] = round(r["ASST"] / r["MSTR"], 5)
        # short-window changes for the dip rule (trading days, no reliance on prior highs)
        n = dc["lookback_days"]
        if i >= n:
            b = rows[i - n]
            for key, out in (("btc", "btc_chg"), ("MSTR", "mstr_chg"), ("ASST", "asst_chg"), ("mstr_mnav", "mstr_mnav_chg"), ("asst_mnav", "asst_mnav_chg")):
                if r.get(key) and b.get(key): r[out] = round(r[key] / b[key] - 1, 4)

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
        dip = r.get("btc_chg") is not None and r["btc_chg"] <= -dc["btc_drop_pct"]
        hardest = None
        m, a_ = r.get("mstr_mnav_chg"), r.get("asst_mnav_chg")
        if m is not None and a_ is not None:
            if abs(m - a_) > dc.get("tie_tolerance", 0.005): hardest = "MSTR" if m < a_ else "ASST"
            elif r.get("mstr_chg") is not None and r.get("asst_chg") is not None: hardest = "MSTR" if r["mstr_chg"] < r["asst_chg"] else "ASST"
        per = {}
        for asset, pref in (("MSTR", "STRC"), ("ASST", "SATA")):
            ps = r.get("peg_" + pref)
            act = {"ON": "BUY", "NEAR": "HOLD", "WATCH": "WATCH", "SELL": "SELL", None: "NO_DATA"}[ps]
            if both_oh: act = CFG["mnav"].get("both_overheated_action", "SELL_TO_BTC")
            per[asset] = {"peg_state": ps, "preferred": pref, "preferred_flywheel_state": AN.fly_label(ps), "action": act, "mnav": r.get(asset.lower() + "_mnav"),
                          "mnav_pct_1y": r.get(asset.lower() + "_mnav_pct"), "overheated": oh[asset.lower()],
                          "price_chg_dip_window": r.get(asset.lower() + "_chg"), "mnav_chg_dip_window": r.get(asset.lower() + "_mnav_chg")}
        out.update(per=per, both_overheated=both_oh, rotation=rot, dip=dip, dip_target=hardest if dip else None, cheapened_most=hardest)
        # headline
        if both_oh: head = "BOTH OVERHEATED -> SELL_TO_BTC: sell MSTR + ASST, hold BTC"
        else:
            buys = [a for a in ("MSTR", "ASST") if per[a]["action"] == "BUY"]
            sells = [a for a in ("MSTR", "ASST") if per[a]["action"] == "SELL"]
            parts = []
            if buys:
                if len(buys) == 2 and rot != "NEUTRAL": parts.append("BUY " + ("ASST" if rot == "FAVOR_ASST" else "MSTR") + " (both flywheels ON; mNAV rotation tilts it)")
                else: parts.append("; ".join(f"BUY {a} ({PREF_OF[a]} ON \u2192 flywheel on for {a})" for a in buys))
            if sells: parts.append("; ".join(f"SELL/TRIM {a} ({PREF_OF[a]} stalled \u2192 flywheel off for {a})" for a in sells))
            watch = [a for a in ("MSTR", "ASST") if per[a]["action"] == "WATCH"]
            if watch: parts.append("; ".join(f"WATCH {a} ({PREF_OF[a]} slowing \u2192 flywheel off for {a})" for a in watch))
            if dip: parts.append(f"DIP-BUY: BTC {r['btc_chg']*100:.0f}% in {dc['lookback_days']} trading days; buy {hardest} (mNAV fell most)")
            if not parts: parts.append("No strong signal - hold cash / stay patient")
            head = "; ".join(parts)
        out["headline"] = head
        return out

    for r in rows: r["sig"] = day_signal(r)

    # ---- single recommendation (BUY_MSTR / BUY_ASST / STAY_BTC) per day
    import analytics
    ASSETS = CFG.get("assets") or [{"ticker": "MSTR", "key": "mstr", "preferred": "STRC"}, {"ticker": "ASST", "key": "asst", "preferred": "SATA"}]
    for r in rows:
        r["rec"] = analytics.recommendation(r, ASSETS, CFG)
        r["rec_hash"] = analytics.rec_hash(r["rec"])
        for A in ASSETS:
            a = A["key"]
            if r.get(a + "_btc") and r.get(a + "_shares"): r[a + "_bps"] = round(r[a + "_btc"] / r[a + "_shares"] * 1e3, 6)
            if r.get(a + "_btc") and r.get(a + "_basic_shares"): r[a + "_bps_basic"] = round(r[a + "_btc"] / r[a + "_basic_shares"] * 1e3, 6)

    # ---- valuation zones (who's cheap vs expensive)
    vc = CFG.get("valuation", {"cheap_below_mnav": 1.0, "fair_max_percentile": 0.5})
    def zone(v, p):
        if v is None: return None
        if v >= mc["overheated_min_mnav"] or (p is not None and p >= mc["overheated_percentile"]): return "OVERVALUED"
        if v < vc["cheap_below_mnav"]: return "CHEAP"
        if p is None or p <= vc["fair_max_percentile"]: return "FAIR"
        return "RICH"
    for r in rows:
        for a in ("mstr", "asst"):
            r[a + "_zone"] = zone(r.get(a + "_mnav"), r.get(a + "_mnav_pct"))
            for bs in ("basic", "ev"):
                r[a + "_zone_" + bs] = zone(r.get(a + "_mnav_" + bs), r.get(a + "_mnav_" + bs + "_pct"))
    def verdict(r, bs=""):
        sfx = "_" + bs if bs else ""
        z, m, s = r.get("spread_z" + sfx), r.get("mstr_mnav" + sfx), r.get("asst_mnav" + sfx)
        if m is None or s is None: return None
        lower = "MSTR" if m < s else "ASST"
        if z is not None and z <= mc["spread_z_favor_asst"]:
            return f"ASST is cheaper than MSTR right now (spread z {z:+.2f}: ASST's premium is unusually low vs MSTR's)"
        if z is not None and z >= mc["spread_z_favor_mstr"]:
            return f"MSTR is cheaper than ASST right now (spread z {z:+.2f}: ASST's premium is unusually high vs MSTR's)"
        zt = f"{z:+.2f}" if z is not None else "n/a"
        return (f"Neither is clearly cheaper vs its usual gap (spread z {zt}, within ±1); "
                f"{lower} has the lower mNAV today ({min(m, s):.2f}x vs {max(m, s):.2f}x)")

    def rel_value(r, bs):
        """Display/alert summary of the ASST-vs-MSTR relationship. Verdict uses the existing rotation rule
        (spread_z on the ASST-MSTR mNAV difference vs config thresholds); ratio stats are context."""
        sfx = "_" + bs
        z = r.get("spread_z" + sfx)
        if r.get("mstr_mnav" + sfx) is None or r.get("asst_mnav" + sfx) is None: return None
        if z is not None and z <= mc["spread_z_favor_asst"]: code, txt = "ASST_CHEAP", "ASST cheap vs usual spread"
        elif z is not None and z >= mc["spread_z_favor_mstr"]: code, txt = "MSTR_CHEAP", "MSTR cheap vs usual spread"
        else: code, txt = "NORMAL", "Spread normal"
        return {"basis": bs, "lookback_trading_days": mc["lookback_trading_days"],
                "mstr_mnav": r.get("mstr_mnav" + sfx), "asst_mnav": r.get("asst_mnav" + sfx),
                "ratio_now": r.get("mnav_ratio" + sfx), "ratio_mean_1y": r.get("ratio_mean" + sfx), "ratio_median_1y": r.get("ratio_median" + sfx),
                "ratio_sd_1y": r.get("ratio_sd" + sfx), "ratio_z": r.get("ratio_z" + sfx), "ratio_pct_1y": r.get("ratio_pct" + sfx),
                "diff_now": r.get("mnav_spread" + sfx), "diff_mean_1y": r.get("spread_mean" + sfx), "diff_sd_1y": r.get("spread_sd" + sfx),
                "diff_z": z, "diff_pct_1y": r.get("spread_pct" + sfx),
                "verdict": code, "verdict_text": txt, "cheaper_vs_usual": {"ASST_CHEAP": "ASST", "MSTR_CHEAP": "MSTR"}.get(code),
                "rule": f"verdict from spread_z (ASST-MSTR mNAV difference vs trailing {mc['lookback_trading_days']}d mean/sd): "
                        f"<= {mc['spread_z_favor_asst']} ASST cheap, >= {mc['spread_z_favor_mstr']} MSTR cheap; ratio stats are context only"}

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

    last = rows[-1]
    def asset_val(A, a, bs):
        v = last.get(a + "_mnav_" + bs)
        return {"mnav": v, "mnav_pct_1y": last.get(a + "_mnav_" + bs + "_pct"), "zone": last.get(a + "_zone_" + bs)}
    BASIS_LABEL = {"basic": "mNAV (market standard): price x basic shares / BTC NAV",
                   "ev": "mNAV all-in (EV): (price x basic shares + debt + preferred notional - cash) / BTC NAV",
                   "diluted": "diluted equity mNAV: price x assumed-diluted shares / BTC NAV"}

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
    # ---- odds jumps (from whatever markets.json now holds)
    oj = {"jumps": [], "summary": []}
    try:
        import odds_jumps
        src = json.loads((DATA / "markets.json").read_text()).get("events", []) if (DATA / "markets.json").exists() else pm
        oj = odds_jumps.build(src, CFG["odds_jumps"], today)
        oj["generated_utc"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        (DATA / "odds_jumps.json").write_text(json.dumps(oj, indent=1, ensure_ascii=False))
    except Exception as e:
        log("odds jumps failed", e)

    # ---- analytics
    start = CFG["window_start"]
    rb = CFG.get("recommendation", {}).get("overheat_basis", "ev")
    dil_from = {"ASST": min([e["date"] for e in a_ev if e.get("shares_basis") == "assumed_fully_diluted"] or [None])}
    pc_ = CFG["peg"]
    flips = analytics.signal_flips(rows, ASSETS, start, rb)
    lc = None
    for p_, r_ in zip(rows, rows[1:]):
        if p_["rec_hash"] != r_["rec_hash"]: lc = {"date": r_["date"], "old": p_["rec"]["action"], "new": r_["rec"]["action"],
                                                    "old_hash": p_["rec_hash"], "new_hash": r_["rec_hash"]}
    lrc = None
    for p_, r_ in zip(rows, rows[1:]):
        if p_["rec"]["action"] != r_["rec"]["action"]: lrc = {"date": r_["date"], "old": p_["rec"]["action"], "new": r_["rec"]["action"]}
    if lc:
        po = next(r_ for r_ in rows if r_["rec_hash"] == lc["old_hash"])
        pn = next(r_ for r_ in rows if r_["rec_hash"] == lc["new_hash"])
        lc["what"] = ", ".join(f"{x['preferred']} {AN.fly_label(po['rec']['per_asset'][t]['peg_state'])} \u2192 {AN.fly_label(x['peg_state'])} (flywheel for {t})" for t, x in pn["rec"]["per_asset"].items()
                               if po["rec"]["per_asset"][t]["peg_state"] != x["peg_state"]) or "recommendation/overheat change"
    sources = {
        "MSTR": {"holdings": max(([h["date"], doc["url"]] for doc in mdocs for h in doc["parsed"].get("holdings", [])), default=None),
                 **{k: (lambda e: [e[0], e[2], e[3]])(cap["MSTR"][k][-1]) for k in ("basic_shares", "debt_m", "pref_usd_shares", "cash_m") if cap["MSTR"].get(k)},
                 "kpi_api": "https://api.strategy.com/btc/mstrKpiData"},
        "ASST": {"holdings": [a_ev[-1]["date"], a_ev[-1].get("src")] if a_ev else None,
                 **{k: (lambda e: [e[0], e[2], e[3]])(cap["ASST"][k][-1]) for k in ("basic_shares", "debt_m", "sata_shares", "sats_shares", "cash_m") if cap["ASST"].get(k)}}}
    ana = {
        "assets": ASSETS,
        "flywheel": {A["preferred"]: analytics.flywheel(rows, A["preferred"], A["ticker"], pc_) for A in ASSETS},
        "btc_yield": analytics.btc_yield(rows, ASSETS, dil_from),
        "btc_yield_diluted_from": dil_from,
        "backtest": {"btc_idle": analytics.backtest(rows, ASSETS, start, "BTC"), "cash_idle": analytics.backtest(rows, ASSETS, start, "CASH")},
        "peg_stretches": [s_ for A in ASSETS for s_ in analytics.peg_stretches(rows, A["preferred"], A["key"], A["ticker"])],
        "atm": analytics.atm_summary(fuel, prices, pc_, cap["ASST"].get("sats_shares", []), rows[-1]["date"]),
        "buy_flags": analytics.buy_flags(rows[-1], ASSETS, CFG),
        "signal_events": analytics.signal_events(rows, ASSETS, rb),
        "signal_flips": flips, "last_changed": lc, "last_recommendation_change": lrc, "sources": sources}
    for k in ("cash_idle",):  # keep only stats + curve for the variant
        ana["backtest"][k].pop("dates", None); ana["backtest"][k].pop("positions", None); ana["backtest"][k].pop("periods", None)
        ana["backtest"][k]["curves"] = {"rules": ana["backtest"][k]["curves"]["rules"]}
    gen0 = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    slog = analytics.update_signal_log(DATA / "signal_log.json", rows[-1]["rec"], rows[-1]["date"], gen0, rows, ASSETS, rb)
    ana["signal_log"] = slog["entries"]

    out_rows = [r for r in rows if r["date"] >= (dt.date.fromisoformat(start) - dt.timedelta(days=200)).isoformat()]
    gen = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    dash = {"generated_utc": gen, "config": CFG, "rows": out_rows, "fuel": fuel,
            "asst_events": a_ev, "mstr_holdings_edgar": edgar_h,
            "latest_btc_spot": {"date": max(btc), "price": btc[max(btc)]}, "big_moves": big, "signal_basis": BASIS, "basis_labels": BASIS_LABEL,
            "capital_residuals": cap["MSTR"]["residuals"], "strategy_kpi_latest": cap["MSTR"].get("strategy_kpi_latest"),
            "sats": prices.get("SATS", {}), "analytics": ana}
    (DATA / "dashboard.json").write_text(json.dumps(dash, separators=(",", ":")))

    lrec = last["rec"]
    sig = {"recommendation": lrec["action"], "recommendation_reason": lrec["reason"], "also_valid": lrec.get("also_valid"),
           "recommendation_notes": lrec.get("notes", []),
           "signal_hash": last["rec_hash"], "last_changed": lc, "last_recommendation_change": lrc,
           "_hash_note": "signal_hash changes when the recommendation, also_valid, any preferred peg state or any overheated flag changes; last_changed is the most recent trading day it changed.",
           "generated_utc": gen, "as_of": last["date"], "disclaimer": "Rules-based indicator, not financial advice.",
           "recommendation_detail": lrec,
           "flywheel": ana["flywheel"],
           "buy_flags": ana["buy_flags"],
           "btc_yield": {t: {k: (v.get("value") if isinstance(v, dict) else v) for k, v in y.items()} for t, y in ana["btc_yield"].items()},
           "headline": last["sig"]["headline"], "per_asset": last["sig"]["per"],
           "both_overheated": last["sig"]["both_overheated"], "rotation": last["sig"]["rotation"],
           "dip": {"active": last["sig"]["dip"], "target": last["sig"]["dip_target"], "lookback_trading_days": dc["lookback_days"],
                   "trigger_btc_drop": dc["btc_drop_pct"], "btc_change": last.get("btc_chg"), "cheapened_most": last["sig"]["cheapened_most"],
                   "mstr_price_change": last.get("mstr_chg"), "asst_price_change": last.get("asst_chg"),
                   "mstr_mnav_change": last.get("mstr_mnav_chg"), "asst_mnav_change": last.get("asst_mnav_chg")},
           "peg": {p: {"price": last.get(p), "state": last.get("peg_" + p), "flywheel_state": AN.fly_label(last.get("peg_" + p)),
                       "distance_from_par": round(last[p] - pc["par"], 3) if last.get(p) else None} for p in ("STRC", "SATA")},
           "signal_basis": BASIS, "signal_basis_label": BASIS_LABEL[BASIS],
           "values": {k: last.get(k) for k in ("MSTR", "ASST", "btc", "mstr_mnav", "asst_mnav", "mnav_avg", "mnav_spread",
                                               "spread_z", "ratio", "mstr_mnav_basic", "asst_mnav_basic", "mstr_mnav_ev", "asst_mnav_ev",
                                               "mstr_mnav_diluted", "asst_mnav_diluted", "mstr_ev_m", "asst_ev_m", "btc_chg", "mstr_chg", "asst_chg", "mstr_mnav_chg", "asst_mnav_chg", "mstr_nav_ps", "asst_nav_ps")},
           "valuation": {**{A: {"signal_basis": BASIS, "zone": last.get(a + "_zone"),
                                 "market_standard": asset_val(A, a, "basic"), "all_in_ev": asset_val(A, a, "ev"),
                                 "diluted_equity_mnav": last.get(a + "_mnav_diluted"),
                                 "price": last.get(A), "btc_held": last.get(a + "_btc"), "basic_shares": last.get(a + "_basic_shares"),
                                 "debt_m": last.get(a + "_debt_m"), "preferred_notional_m": last.get(a + "_pref_m"), "cash_m": last.get(a + "_cash_m"),
                                 "enterprise_value_m": last.get(a + "_ev_m"), "btc_nav_m": round(last[a + "_btc"] * last["btc"] / 1e6) if last.get(a + "_btc") else None,
                                 "btc_nav_per_basic_share": round(last[a + "_btc"] * last["btc"] / last[a + "_basic_shares"], 2) if last.get(a + "_basic_shares") else None}
                             for A, a in (("MSTR", "mstr"), ("ASST", "asst"))},
                         "spread_z": {"basic": last.get("spread_z_basic"), "ev": last.get("spread_z_ev")},
                         "relative_verdict": {"basic": verdict(last, "basic"), "ev": verdict(last, "ev")},
                         "relative_value": {bs: rel_value(last, bs) for bs in ("ev", "basic")}},
           "latest_big_move": big[0] if big else None,
           "big_moves_last_30d": [m for m in big if m["date"] >= (today - dt.timedelta(days=30)).isoformat()],
           "upcoming_events_7d": upcoming,
           "odds_jumps": oj.get("jumps", []), "key_odds": oj.get("summary", []),
           "prediction_markets": [{"source": x["source"], "label": x.get("label"), "url": x.get("url"),
                                   "top": sorted([{"outcome": o["outcome"], "yes": o["yes"]} for o in x.get("outcomes", []) if o.get("yes") is not None],
                                                 key=lambda o: -o["yes"])[:4]} for x in pm if not x.get("error")],
           "thresholds": {k: v for k, v in CFG.items() if k != "prediction_markets"}}
    # state-change log (for monitors)
    prev = rows[-2]["sig"] if len(rows) > 1 else None
    sig["changed_since_prev_close"] = bool(prev and (prev["headline"] != last["sig"]["headline"]))
    sig["prev_headline"] = prev["headline"] if prev else None
    (ROOT / "signals.json").write_text(json.dumps(sig, indent=2))
    # flat CSV of the daily table (for spreadsheets)
    import csv
    cols = sorted({k for r in out_rows for k, v in r.items() if not isinstance(v, (dict, list))})
    cols = ["date"] + [c for c in cols if c != "date"]
    with open(DATA / "rows.csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(cols + ["recommendation", "headline_legacy"])
        for r in out_rows: w.writerow([r.get(c, "") for c in cols] + [r["rec"]["action"], r["sig"]["headline"]])
    # past BUY/SELL events per asset (display-only replay of the daily recommendation)
    with open(DATA / "signal_events.csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["date", "asset", "signal", "all_in_mnav_ev", "held_since", "from", "to", "reason", "full_reason"])
        for e in ana["signal_events"]["events"]:
            w.writerow([e["date"], e["asset"], e["signal"], e["mnav_ev"], e["held_since"] or "", e["from"] or "", e["to"], e["reason"], e["full_reason"]])
    log("latest", json.dumps(sig, indent=1)[:3000])

if __name__ == "__main__":
    main()
