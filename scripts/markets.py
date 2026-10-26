"""Prediction-market snapshot + history from free public APIs (no key needed).

Polymarket: https://gamma-api.polymarket.com/events?slug=<slug>   (current odds)
            https://clob.polymarket.com/prices-history?market=<yes_token>&interval=max&fidelity=1440 (daily history)
Kalshi:     https://api.elections.kalshi.com/trade-api/v2/events/<event_ticker>?with_nested_markets=true
            https://api.elections.kalshi.com/trade-api/v2/series/<series>/markets/<ticker>/candlesticks?period_interval=1440
Which events are tracked is set in config.json -> prediction_markets.
"""
import json, time, sys, datetime as dt
import requests

UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"

def log(*a): print(*a, file=sys.stderr, flush=True)

def get(url, params=None):
    for i in range(3):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=30); r.raise_for_status(); return r.json()
        except Exception as e:
            log("pm retry", url, e); time.sleep(2 + 2 * i)
    return None

def f(x):
    try: return float(x)
    except Exception: return None

def polymarket(item, hist_n):
    d = get(f"{GAMMA}/events", {"slug": item["slug"]})
    if not d: return {"source": "polymarket", "id": item["slug"], "label": item.get("label"), "error": "not found / API error"}
    e = d[0]
    outs = []
    for m in e.get("markets", []):
        if m.get("closed") or not m.get("active") or not m.get("outcomePrices"): continue
        try:
            prices = json.loads(m["outcomePrices"]); toks = json.loads(m.get("clobTokenIds") or "[]")
        except Exception: continue
        name = m.get("groupItemTitle") or m.get("question")
        outs.append({"outcome": name, "question": m.get("question"), "yes": f(prices[0]),
                     "volume": f(m.get("volume")) or 0, "token": toks[0] if toks else None, "end": m.get("endDate")})
    outs.sort(key=lambda o: -o["volume"])
    outs = outs[: item.get("max_outcomes", 12)]
    for o in outs[:hist_n]:
        if not o["token"]: continue
        h = get(f"{CLOB}/prices-history", {"market": o["token"], "interval": "max", "fidelity": 1440})
        if h and h.get("history"):
            o["history"] = [[dt.datetime.fromtimestamp(p["t"], dt.timezone.utc).date().isoformat(), p["p"]] for p in h["history"]]
    for o in outs: o.pop("token", None)
    return {"source": "polymarket", "id": item["slug"], "label": item.get("label") or e.get("title"), "title": e.get("title"),
            "url": f"https://polymarket.com/event/{item['slug']}", "end": e.get("endDate"),
            "volume": f(e.get("volume")), "outcomes": outs}

def kalshi(item, hist_n):
    d = get(f"{KALSHI}/events/{item['event']}", {"with_nested_markets": "true"})
    if not d or "event" not in d: return {"source": "kalshi", "id": item["event"], "label": item.get("label"), "error": "not found / API error"}
    e = d["event"]; ms = e.get("markets") or d.get("markets") or []
    outs = []
    for m in ms:
        if m.get("status") not in (None, "active", "open", "initialized"): continue
        last = f(m.get("last_price_dollars")); bid = f(m.get("yes_bid_dollars")); ask = f(m.get("yes_ask_dollars"))
        mid = (bid + ask) / 2 if bid is not None and ask is not None and ask > 0 else last
        outs.append({"outcome": m.get("yes_sub_title") or m.get("subtitle") or m["ticker"], "ticker": m["ticker"],
                     "yes": last if last else mid, "yes_bid": bid, "yes_ask": ask,
                     "volume": f(m.get("volume_fp") or m.get("volume")) or 0, "end": m.get("close_time")})
    outs.sort(key=lambda o: -o["volume"])
    outs = outs[: item.get("max_outcomes", 12)]
    end = int(time.time()); start = end - 86400 * 365
    for o in outs[:hist_n]:
        time.sleep(0.6)
        c = get(f"{KALSHI}/series/{e.get('series_ticker', item.get('series'))}/markets/{o['ticker']}/candlesticks",
                {"start_ts": start, "end_ts": end, "period_interval": 1440})
        if c and c.get("candlesticks"):
            o["history"] = [[dt.datetime.fromtimestamp(k["end_period_ts"], dt.timezone.utc).date().isoformat(),
                             f((k.get("price") or {}).get("close_dollars")) or f((k.get("yes_bid") or {}).get("close_dollars"))]
                            for k in c["candlesticks"]]
            o["history"] = [h for h in o["history"] if h[1] is not None]
    return {"source": "kalshi", "id": item["event"], "label": item.get("label") or e.get("title"), "title": e.get("title"),
            "url": f"https://kalshi.com/markets/{e.get('series_ticker', '').lower()}", "outcomes": outs}

def fetch_all(cfg):
    hist_n = cfg.get("history_outcomes", 3)
    out = []
    for it in cfg.get("polymarket", []):
        try: out.append(dict(polymarket(it, hist_n), group=it.get("group")))
        except Exception as e: out.append({"source": "polymarket", "id": it["slug"], "group": it.get("group"), "error": str(e)})
    for it in cfg.get("kalshi", []):
        try: out.append(dict(kalshi(it, hist_n), group=it.get("group")))
        except Exception as e: out.append({"source": "kalshi", "id": it["event"], "group": it.get("group"), "error": str(e)})
    return out
