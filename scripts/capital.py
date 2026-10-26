#!/usr/bin/env python3
"""Capital-structure timelines for the all-in (EV) mNAV.

EV mNAV = (price x basic shares + debt at notional + preferred notional - cash/USD reserve) / (BTC held x BTC price)

Each input is a daily step function built from filings:
  * level anchors: 10-Q/10-K cover share counts, XBRL quarter-end balances (SEC companyfacts),
    weekly 8-K balances, and Strategy's live KPI API (api.strategy.com) for "today";
  * dated flows between anchors: weekly 8-K ATM sales / repurchases, preferred IPOs and debt
    issuances (data/capital_events.json, each with a filing link).
value(d) = level of the last anchor on or before d + (flows after that anchor up to d).
Before the first anchor: back-cast from it (basic shares, debt) or rebuilt from zero (preferreds).
Each anchor's "residual" (anchor minus what the flows predicted) is recorded so gaps are visible.
"""
import json, re, html, time, datetime as dt
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CAP_VERSION = 1
MONTHS = {m: i for i, m in enumerate(["January","February","March","April","May","June","July","August",
                                       "September","October","November","December"], 1)}
SER = ["STRK", "STRF", "STRD", "STRC", "STRE"]
N = r"([0-9][0-9,]*(?:\.[0-9]+)?|-|—|–)"
MSTR_SPLIT = ("2024-08-08", 10)  # 10-for-1 split effective Aug 8 2024; Yahoo prices are split-adjusted

def num(s):
    s = s.replace(",", "").replace("$", "").strip()
    return 0.0 if s in ("-", "—", "–", "") else float(s)

def pdate(s):
    m = re.match(r"([A-Z][a-z]+) ?(\d{1,2}) ?, ?(\d{4})", s.strip())
    return dt.date(int(m.group(3)), MONTHS[m.group(1)], int(m.group(2))).isoformat()

def text_of(url, ua):
    t = requests.get(url, headers=ua, timeout=40).text
    t = re.sub(r"<[^>]+>", " ", t); t = html.unescape(t); return re.sub(r"\s+", " ", t)

# ---------------- MSTR weekly 8-K parser (ATM / repurchases / USD reserve) ----------------
def parse_mstr_8k(t):
    p = {"atm": [], "rep": [], "levels": {}}
    for seg in re.split(r"During Period ", t)[1:]:
        m = re.match(r"([A-Z][a-z]+ \d{1,2}, \d{4}) to ([A-Z][a-z]+ \d{1,2}, \d{4})", seg)
        if not m: continue
        end = pdate(m.group(2)); s = seg[:3000]
        cut = re.search(r"BTC Update|Repurchase Program|During Period", s[60:]); s = s[:cut.start() + 60] if cut else s
        if "Repurchased" in s[:400]:
            for S in SER + ["MSTR"]:
                mm = re.search(S + r" Stock(?: \(\d\))? " + N + r" \$ ?" + N, s)
                if mm and num(mm.group(1)): p["rep"].append({"end": end, "sec": S, "shares": num(mm.group(1)), "usd_m": num(mm.group(2))})
            continue
        if "Sold" not in s[:600] and "ATM" not in s[:600]: continue
        for S in SER:
            mm = (re.search(S + r" Stock(?: \(\d\))? (?:[A-Za-z0-9.%\- ]{0,80}? )?" + N + r" \$ ?" + N + r" \$ ?" + N + r" \$ ?" + N, s)
                  or re.search(S + r" ATM " + N + r" (?:" + S + r" Shares )?\$ ?" + N, s))
            if mm and num(mm.group(1)): p["atm"].append({"end": end, "sec": S, "shares": num(mm.group(1)), "notional_m": num(mm.group(2))})
        mm = (re.search(r"MSTR Stock(?: \(\d\))? (?:Class A Common Stock )?" + N + r" \$", s)
              or re.search(r"(?:Common|MSTR) ATM " + N + r" (?:MSTR Shares )?(?:- )?\$", s))
        if mm and num(mm.group(1)): p["atm"].append({"end": end, "sec": "MSTR", "shares": num(mm.group(1))})
    for mm in re.finditer(r"during the period between ([A-Z][a-z]+ \d{1,2}, \d{4}) and ([A-Z][a-z]+ \d{1,2}, \d{4}), the Company had sold an aggregate of ([0-9,]+) Shares", t):
        p["atm"].append({"end": pdate(mm.group(2)), "sec": "MSTR", "shares": num(mm.group(3))})
    f = lambda v, u: num(v) * (1000 if u == "billion" else 1)
    mm = re.search(r"As of ([A-Z][a-z]+ \d{1,2}, \d{4}), the balances? of the USD Reserve and USD Cash were \$ ?([0-9.,]+) (billion|million) and \$ ?([0-9.,]+) (billion|million)", t)
    if mm:
        p["levels"]["usd_reserve"] = [pdate(mm.group(1)), f(mm.group(2), mm.group(3))]
        p["levels"]["usd_cash"] = [pdate(mm.group(1)), f(mm.group(4), mm.group(5))]
    else:
        mm = (re.search(r"As of ([A-Z][a-z]+ \d{1,2}, \d{4}), the balance of the USD Reserve (?:is|was) \$ ?([0-9.,]+) (billion|million)", t)
              or re.search(r"On ([A-Z][a-z]+ \d{1,2}, \d{4}), Strategy Inc \(“Strategy”\) announced that it has established a US dollar reserve \(“USD Reserve”\) of \$ ?([0-9.,]+) (billion|million)", t))
        if mm: p["levels"]["usd_reserve"] = [pdate(mm.group(1)), f(mm.group(2), mm.group(3))]
    return p

def sec_8ks(cik, since, ua, cache, parser):
    sub = requests.get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json", headers=ua, timeout=30).json()
    r = sub["filings"]["recent"]; out = []
    for f, d, acc, doc in zip(r["form"], r["filingDate"], r["accessionNumber"], r["primaryDocument"]):
        if f != "8-K" or d < since: continue
        key = "8k:" + acc
        if key not in cache or cache[key].get("v") != CAP_VERSION:
            base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/"
            try:
                texts = [text_of(base + doc, ua)]; time.sleep(0.12)
                idx = requests.get(base + "index.json", headers=ua, timeout=30).json(); time.sleep(0.12)
                for it in idx["directory"]["item"]:
                    n = it["name"].lower()
                    if re.search(r"ex-?99", n) and n.endswith(".htm"):
                        texts.append(text_of(base + it["name"], ua)); time.sleep(0.12)
            except Exception as e:
                print("8-K fetch failed", acc, e); continue
            parsed = {"atm": [], "rep": [], "levels": {}}
            for t in texts:
                q = parser(t); parsed["atm"] += q["atm"]; parsed["rep"] += q["rep"]
                for k, v in q["levels"].items(): parsed["levels"].setdefault(k, v)
            cache[key] = {"v": CAP_VERSION, "filed": d, "url": base + doc, "parsed": parsed}
        out.append(cache[key])
    return out

# ---------------- 10-Q / 10-K cover share counts ----------------
def loose(phrase):  # tolerate tag-split words ("r egistrant", "a nd")
    return r"\s?".join(re.escape(c) if c != " " else r"\s+" for c in phrase)

COVER = re.compile(r"(?:As|as)\s+of\s+([A-Z][a-z]+\s?\d{1,2}\s?,\s?\d{4})\s?,?\s+" + loose("the registrant had") + r"\s+([0-9][0-9,\s]*[0-9])\s+"
                   + loose("and") + r"\s+([0-9][0-9,\s]*[0-9])\s+" + loose("shares of class A common stock and class B common stock"), re.I)

def covers(cik, since, ua, cache):
    sub = requests.get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json", headers=ua, timeout=30).json()
    r = sub["filings"]["recent"]; out = []
    for f, d, acc, doc in zip(r["form"], r["filingDate"], r["accessionNumber"], r["primaryDocument"]):
        if f not in ("10-Q", "10-K") or d < since: continue
        key = "cover:" + acc
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}"
        if key not in cache or cache[key].get("v") != CAP_VERSION:
            try:
                t = text_of(url, ua)[:300000]; time.sleep(0.12)
            except Exception as e:
                print("cover fetch failed", acc, e); continue
            m = COVER.search(t)
            cache[key] = {"v": CAP_VERSION, "filed": d, "form": f, "url": url,
                          "parsed": ({"date": pdate(re.sub(r"\s+", " ", m.group(1))), "class_a": num(m.group(2).replace(" ", "")),
                                      "class_b": num(m.group(3).replace(" ", ""))} if m else None)}
        if cache[key]["parsed"]: out.append(cache[key])
        else: print("cover share count not found", url)
    return out

# ---------------- XBRL quarter-end balances ----------------
def xbrl(cik, ua, concepts, cache_file):
    try:
        cf = requests.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json", headers=ua, timeout=60).json()["facts"]["us-gaap"]
        out = {}
        for k in concepts:
            if k not in cf: continue
            for u, arr in cf[k]["units"].items():
                pts = {}
                for a in arr:
                    if "start" in a or a["end"] < "2024-01-01": continue
                    pts[a["end"]] = {"val": a["val"], "form": a["form"], "accn": a["accn"], "filed": a["filed"]}
                out[k] = dict(sorted(pts.items()))
        cache_file.write_text(json.dumps(out, indent=1)); return out
    except Exception as e:
        print("XBRL fetch failed, using cache", e)
        return json.loads(cache_file.read_text()) if cache_file.exists() else {}

def xbrl_url(cik, accn): return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn.replace('-', '')}/"

# ---------------- Strategy KPI API (today's anchor) ----------------
def strategy_kpi(ua_web):
    hist_f = DATA / "strategy_kpi_history.json"
    hist = json.loads(hist_f.read_text()) if hist_f.exists() else []
    try:
        k = requests.get("https://api.strategy.com/btc/mstrKpiData", headers=ua_web, timeout=30).json()
        k = k[0] if isinstance(k, list) else k
        lst = requests.get("https://api.strategy.com/btc/kpiData", headers=ua_web, timeout=30).json()
        per = {}
        for x in lst:
            t = (x.get("company") or x.get("ticker") or "").upper()
            if t in ["MSTR"] + SER and x.get("ufPrice") and x.get("marketCap"):
                per[t] = {"shares": round(float(str(x["marketCap"]).replace(",", "")) * 1e6 / float(str(x["ufPrice"]).replace(",", ""))),
                          "price": float(str(x["ufPrice"]).replace(",", ""))}
        g = lambda key: float(str(k[key]).replace(",", ""))
        snap = {"fetched_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "timestamp_utc": k.get("timeStampUtc"),
                "market_cap_m": g("marketCap"), "ev_m": g("entVal"), "debt_m": g("debt"), "pref_m": g("pref"), "price": g("price"),
                "per_ticker": per}
        snap["cash_m"] = round(snap["market_cap_m"] + snap["debt_m"] + snap["pref_m"] - snap["ev_m"], 1)
        snap["date"] = dt.datetime.now(dt.timezone(dt.timedelta(hours=-4))).date().isoformat()
        hist = [h for h in hist if h["date"] != snap["date"]] + [snap]
        hist_f.write_text(json.dumps(hist[-400:], indent=1))
    except Exception as e:
        print("Strategy KPI API failed; using stored history", e)
    return hist

# ---------------- step-function machinery ----------------
def anchored(flows, anchors, before="backcast"):
    """flows: [(date, delta, src)], anchors: [(date, level, src, approx)] -> steps [[date, value, src, approx]], residuals"""
    flows = sorted(flows, key=lambda x: x[0]); anchors = sorted(anchors, key=lambda x: x[0])
    def cum(d): return sum(f[1] for f in flows if f[0] <= d)
    dates = sorted({f[0] for f in flows} | {a[0] for a in anchors})
    steps, resid = [], []
    for d in dates:
        prev = [a for a in anchors if a[0] <= d]
        fsrc = [f[2] for f in flows if f[0] == d]
        if prev:
            a = prev[-1]; v = a[1] + cum(d) - cum(a[0])
            src, ap = (a[2], a[3]) if a[0] == d else (fsrc[0] if fsrc else a[2], False)
        elif anchors and before == "backcast":
            a = anchors[0]; v = a[1] - (cum(a[0]) - cum(d)); src, ap = (fsrc[0] if fsrc else a[2]), True
        else:
            v = cum(d); src, ap = (fsrc[0] if fsrc else None), False
        steps.append([d, max(v, 0.0), src, ap])
    for i, a in enumerate(anchors):
        if i == 0 and before == "backcast": continue
        pa = anchors[i - 1] if i else None
        pred = (pa[1] + cum(a[0]) - cum(pa[0])) if pa else cum(a[0])
        resid.append({"date": a[0], "anchor": a[1], "predicted_from_flows": round(pred, 3), "residual": round(a[1] - pred, 3), "src": a[2]})
    return steps, resid

def levels(points):  # [(date, level, src, approx)] -> steps (last value per date wins)
    d = {}
    for p in sorted(points, key=lambda x: x[0]): d[p[0]] = [p[0], p[1], p[2], p[3]]
    return list(d.values())

def lookup(steps):
    def f(d):
        v = None
        for s in steps:
            if s[0] <= d: v = s
            else: break
        return v
    return f

# ---------------- build ----------------
def build(ua_sec, ua_web, cache, asst_events, seed):
    ev = json.loads((DATA / "capital_events.json").read_text())
    out = {"generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "version": CAP_VERSION}
    # ===== MSTR =====
    cik = "1050446"
    docs = sec_8ks(cik, "2024-09-01", ua_sec, cache, parse_mstr_8k)
    atm, rep, lv = {}, {}, []
    for doc in docs:
        for a in doc["parsed"]["atm"]: atm[(a["end"], a["sec"])] = dict(a, src=doc["url"])
        for r in doc["parsed"]["rep"]: rep[(r["end"], r["sec"])] = dict(r, src=doc["url"])
        for k, v in doc["parsed"]["levels"].items(): lv.append((k, v[0], v[1], doc["url"]))
    cov = covers(cik, "2024-01-01", ua_sec, cache)
    xb = xbrl(cik, ua_sec, ["LongTermDebt", "CashAndCashEquivalentsAtCarryingValue", "TemporaryEquitySharesOutstanding"], DATA / "xbrl_mstr.json")
    kpi = strategy_kpi(ua_web); k = kpi[-1] if kpi else None
    # basic shares
    flows = [(a["end"], a["shares"], a["src"]) for a in atm.values() if a["sec"] == "MSTR"] + \
            [(r["end"], -r["shares"], r["src"]) for r in rep.values() if r["sec"] == "MSTR"]
    anc = []
    for c in cov:
        p = c["parsed"]; mult = MSTR_SPLIT[1] if p["date"] < MSTR_SPLIT[0] else 1
        anc.append((p["date"], (p["class_a"] + p["class_b"]) * mult, c["url"], False))
    if k and k["per_ticker"].get("MSTR"): anc.append((k["date"], k["per_ticker"]["MSTR"]["shares"], "https://api.strategy.com/btc/kpiData", False))
    m_basic, m_basic_res = anchored(flows, anc, "backcast")
    # debt ($M)
    dflows = [(e["date"], e["delta_m"], e["src"]) for e in ev["MSTR"]["debt_flows"]]
    danc = [(d, v["val"] / 1e6, xbrl_url(cik, v["accn"]), False) for d, v in xb.get("LongTermDebt", {}).items()]
    danc += [(e["date"], e["level_m"], e["src"], e.get("approx", False)) for e in ev["MSTR"]["debt_anchors"]]
    if k: danc.append((k["date"], k["debt_m"], "https://api.strategy.com/btc/mstrKpiData", False))
    m_debt, m_debt_res = anchored(dflows, danc, "backcast")
    # preferreds: USD-stated series and STRE (EUR) tracked separately, in shares
    ipo = ev["MSTR"]["preferred_ipos"]
    def pflows(series):
        f = [(e["date"], e["shares"], e["src"]) for e in ipo if e["series"] in series]
        f += [(a["end"], a["shares"], a["src"]) for a in atm.values() if a["sec"] in series]
        f += [(r["end"], -r["shares"], r["src"]) for r in rep.values() if r["sec"] in series]
        return f
    usd_s = [s for s in SER if s != "STRE"]
    stre_anc = []
    if k and "STRE" in k["per_ticker"]: stre_anc.append((k["date"], k["per_ticker"]["STRE"]["shares"], "https://api.strategy.com/btc/kpiData", False))
    m_stre, m_stre_res = anchored(pflows(["STRE"]), stre_anc, "zero")
    stre_at = lookup(m_stre)
    uanc = []
    for d, v in xb.get("TemporaryEquitySharesOutstanding", {}).items():
        s = stre_at(d); uanc.append((d, v["val"] - (s[1] if s else 0), xbrl_url(cik, v["accn"]), False))
    if k and all(t in k["per_ticker"] for t in usd_s):
        uanc.append((k["date"], sum(k["per_ticker"][t]["shares"] for t in usd_s), "https://api.strategy.com/btc/kpiData", False))
    m_usd, m_usd_res = anchored(pflows(usd_s), uanc, "zero")
    # cash / USD reserve ($M)
    reserve = sorted([(d, v, u) for kk, d, v, u in lv if kk == "usd_reserve"])
    usd_cash = {d: v for kk, d, v, u in lv if kk == "usd_cash"}
    cpts = []
    for d, v in xb.get("CashAndCashEquivalentsAtCarryingValue", {}).items():
        near = [r for r in reserve if abs((dt.date.fromisoformat(r[0]) - dt.date.fromisoformat(d)).days) <= 14]
        if d >= "2025-12-01" and near: continue
        cpts.append((d, v["val"] / 1e6, xbrl_url(cik, v["accn"]), d >= "2025-12-01"))
    for d, v, u in reserve:
        cpts.append((d, v + usd_cash.get(d, 0.0), u, d not in usd_cash))
    if k: cpts.append((k["date"], k["cash_m"], "https://api.strategy.com/btc/mstrKpiData", False))
    m_cash = levels(cpts)
    out["MSTR"] = {"basic_shares": m_basic, "debt_m": m_debt, "pref_usd_shares": m_usd, "pref_stre_shares": m_stre, "cash_m": m_cash,
                   "residuals": {"basic_shares": m_basic_res, "debt_m": m_debt_res, "pref_usd_shares": m_usd_res, "pref_stre_shares": m_stre_res},
                   "strategy_kpi_latest": k, "pref_check": ev["MSTR"].get("preferred_check", [])}
    # ===== ASST =====
    cik = "1920406"
    xa = xbrl(cik, ua_sec, ["CashAndCashEquivalentsAtCarryingValue", "EquitySecuritiesFvNi"], DATA / "xbrl_asst.json")
    a = sorted(asst_events, key=lambda e: e["date"])
    def _basic(e):
        if "shares_basic" in e: return e["shares_basic"]
        return e["shares"] if "shares" in e and e.get("shares_basis") != "assumed_fully_diluted" else None
    basic = levels([(e["date"], _basic(e), e.get("src", "data/asst_seed.json"), "src" not in e) for e in a if _basic(e)])
    pts = [(e["date"], e["sata"], e.get("src", "data/asst_seed.json"), False) for e in a + seed.get("sata_other", []) if "sata" in e]
    pts += [(e["date"], e["sata"], e["src"], e.get("approx", False)) for e in ev["ASST"]["sata_points"]]
    sata = levels(pts)
    spts = [(e["date"], e["sats"], e["src"], False) for e in ev["ASST"]["sats_points"]]
    spts += [(e["date"], e.get("sats", 0), e["src"], False) for e in a if "cash_k" in e and "src" in e and e["date"] >= "2026-10-23"]
    sats = levels(spts)
    cash_x, fv_x = xa.get("CashAndCashEquivalentsAtCarryingValue", {}), xa.get("EquitySecuritiesFvNi", {})
    cp = [(d, (v["val"] + (fv_x.get(d, {}).get("val") or 0)) / 1e6, xbrl_url(cik, v["accn"]), False) for d, v in cash_x.items() if d >= "2025-06-30"]
    weekly = [e for e in a if "cash_k" in e]
    if weekly: cp = [c for c in cp if c[0] < weekly[0]["date"]]
    cp += [(e["date"], (e["cash_k"] + e.get("strc_fv_k", 0)) / 1e3, e.get("src"), False) for e in weekly]
    a_cash = levels(cp)
    a_debt = levels([(e["date"], e["level_m"], e["src"], e.get("approx", False)) for e in ev["ASST"]["debt_anchors"]])
    out["ASST"] = {"basic_shares": basic, "sata_shares": sata, "sats_shares": sats, "cash_m": a_cash, "debt_m": a_debt}
    return out

def ev_inputs(cap, asset, d, eurusd, sv=None):
    sv = sv or {}
    """Return dict of EV inputs (USD) at date d, or None if any piece missing."""
    c = cap[asset]; g = lambda key: (lookup(c[key])(d) or [None, None])[1]
    if asset == "MSTR":
        u, s, debt, cash, sh = g("pref_usd_shares"), g("pref_stre_shares"), g("debt_m"), g("cash_m"), g("basic_shares")
        if None in (debt, cash, sh): return None
        fx = eurusd(d)
        pref = (u or 0) * sv.get("mstr_preferred_stated_value_usd", 100) + (s or 0) * sv.get("mstr_stre_stated_value_eur", 100) * (fx or 0)
        return {"basic_shares": sh, "debt": debt * 1e6, "pref": pref, "cash": cash * 1e6, "stre_shares": s or 0, "eurusd": fx,
                "missing_fx": bool(s and not fx)}
    sh, sa, ss, cash, debt = g("basic_shares"), g("sata_shares"), g("sats_shares"), g("cash_m"), g("debt_m")
    if None in (sh, cash, debt): return None
    return {"basic_shares": sh, "debt": debt * 1e6, "pref": ((sa or 0) + (ss or 0)) * sv.get("asst_preferred_stated_value", 100), "cash": cash * 1e6}
