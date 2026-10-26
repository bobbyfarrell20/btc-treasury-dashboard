"""Odds Jumps: flag tracked prediction markets whose Yes probability moved a lot.

Input: data/markets.json events (daily history per outcome from Polymarket CLOB / Kalshi candlesticks).
Rules (config.json -> odds_jumps): |1-day change| >= jump_24h_pts, or |7-day change| >= jump_7d_pts,
within the last window_days. Mirror outcomes of the same market on the same day are de-duplicated
(largest move kept). 'Likely news' = top Google News RSS result for the group's query around that date
(blank if nothing is found).
"""
import datetime as dt, sys, time, urllib.parse, xml.etree.ElementTree as ET
import requests

def log(*a): print(*a, file=sys.stderr, flush=True)

def _at_or_before(hist, d):
    best = None
    for hd, p in hist:
        if hd <= d: best = (hd, p)
        else: break
    return best

def find_jumps(events, cfg, today):
    j24, j7, win = cfg["jump_24h_pts"] / 100, cfg["jump_7d_pts"] / 100, cfg["window_days"]
    since = (today - dt.timedelta(days=win)).isoformat()
    cands = {}
    for e in events:
        if e.get("error"): continue
        for o in e.get("outcomes", []):
            h = sorted((d, p) for d, p in (o.get("history") or []) if p is not None)
            for idx, (d, p) in enumerate(h):
                if d < since: continue
                dd = dt.date.fromisoformat(d)
                checks = []
                if idx > 0 and (dd - dt.date.fromisoformat(h[idx - 1][0])).days <= 2:
                    checks.append(("24h", h[idx - 1], j24))
                b7 = _at_or_before(h, (dd - dt.timedelta(days=7)).isoformat())
                if b7 and (dd - dt.date.fromisoformat(b7[0])).days <= 10:
                    checks.append(("7d", b7, j7))
                for kind, (bd, bp), thr in checks:
                    ch = p - bp
                    if abs(ch) + 1e-9 < thr: continue
                    key = (e["source"], e["id"], d, kind)
                    row = {"date": d, "window": kind, "source": e["source"], "group": e.get("group"),
                           "market": e.get("label") or e.get("title"), "outcome": o["outcome"],
                           "question": o.get("question"), "url": e.get("url"),
                           "from": round(bp, 4), "to": round(p, 4), "change_pts": round(ch * 100, 1), "from_date": bd}
                    if key not in cands or abs(ch) > abs(cands[key]["change_pts"] / 100): cands[key] = row
    rows = list(cands.values())
    # 7d rows are redundant if a 24h jump on the same market/outcome already explains it: keep only the latest 7d row per outcome
    seen7, out = set(), []
    for r in sorted(rows, key=lambda r: (r["date"], r["window"] == "24h"), reverse=True):
        if r["window"] == "7d":
            k = (r["source"], r["market"])
            if k in seen7: continue
            seen7.add(k)
        out.append(r)
    out.sort(key=lambda r: (r["date"], abs(r["change_pts"])), reverse=True)
    return out

def news_for(query, date):
    d = dt.date.fromisoformat(date)
    q = f"{query} after:{(d - dt.timedelta(days=1)).isoformat()} before:{(d + dt.timedelta(days=1)).isoformat()}"
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20); r.raise_for_status()
        it = ET.fromstring(r.content).find("./channel/item")
        if it is None: return None
        src = it.find("source")
        return {"headline": it.findtext("title"), "url": it.findtext("link"), "published": it.findtext("pubDate"),
                "publisher": src.text if src is not None else None, "via": "Google News RSS"}
    except Exception as e:
        log("news lookup failed", query, e); return None

def summary(events, cfg):
    out = []
    idx = {(e["source"], e["id"]): e for e in events if not e.get("error")}
    for s in cfg.get("summary_markets", []):
        e = idx.get((s["source"], s["id"]))
        if not e: out.append({"short": s["short"], "missing": True}); continue
        outs = [o for o in e.get("outcomes", []) if o.get("yes") is not None]
        if s.get("outcomes"): outs = [o for name in s["outcomes"] for o in outs if o["outcome"] == name]
        else: outs = sorted(outs, key=lambda o: -o["yes"])[: s.get("top", 1)]
        out.append({"short": s["short"], "url": e.get("url"), "source": e["source"],
                    "odds": [{"outcome": o["outcome"], "yes": o["yes"]} for o in outs]})
    return out

def build(events, cfg, today=None):
    today = today or dt.date.today()
    jumps = find_jumps(events, cfg, today)
    if cfg.get("find_news", True):
        cache = {}
        for r in jumps:
            q = cfg.get("news_queries", {}).get(r["group"])
            if not q: r["news"] = None; continue
            k = (q, r["date"])
            if k not in cache: cache[k] = news_for(q, r["date"]); time.sleep(1)
            r["news"] = cache[k]
    return {"rules": {k: cfg[k] for k in ("jump_24h_pts", "jump_7d_pts", "window_days")}, "jumps": jumps, "summary": summary(events, cfg)}
