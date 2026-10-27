import re
"""Derived analytics for the Flywheel Rotation Tracker (no new data sources; everything is computed
from the daily rows built in update_data.py).

- recommendation(): the single BUY_MSTR / BUY_ASST / STAY_BTC call for a day.
- flywheel(): STRC / SATA peg status cards (streak, distance to threshold).
- btc_yield(): % change in BTC per diluted share over 30d / 90d / YTD / 1y windows.
- backtest(): mechanical replay of the recommendation rules vs buy-and-hold.
- peg_stretches(): past confirmed-peg stretches and what the parent / BTC did.
- atm_summary(): STRC / SATA / SATS issuance + days at/near par.
- signal_flips() / update_signal_log(): signal-change history.
"""
import json, hashlib, datetime as dt

D = dt.date.fromisoformat

def _at_or_before(rows, d, key):
    """last row (date <= d) with key present"""
    best = None
    for r in rows:
        if r["date"] > d: break
        if r.get(key) is not None: best = r
    return best

def _at_or_after(rows, d, key=None):
    for r in rows:
        if r["date"] >= d and (key is None or r.get(key) is not None): return r
    return None

def _plus(d, n): return (D(d) + dt.timedelta(days=n)).isoformat()

# ---------------------------------------------------------------- flywheel labels
# STRC / SATA are flywheel indicators for their parents (MSTR / ASST), never trades.
# peg_state keeps the machine codes (ON / NEAR / WATCH / SELL); these are the human labels.
FLY_LABEL = {"ON": "Flywheel ON", "NEAR": "Flywheel slowing", "WATCH": "Flywheel slowing", "SELL": "Flywheel stalled", None: "No data"}
FLY_SHORT = {"ON": "ON", "NEAR": "slowing", "WATCH": "slowing", "SELL": "stalled", None: "no data"}

def fly_label(st): return FLY_LABEL.get(st, "No data")

def fly_effect(pref, st, parent):
    return f"{pref} {FLY_SHORT.get(st, 'no data')} \u2192 flywheel {'on' if st == 'ON' else 'off'} for {parent}"

def relabel_reason(txt):
    """Rewrite older log reasons that used peg-state codes into flywheel language."""
    if not txt: return txt
    par = {"STRC": "MSTR", "SATA": "ASST"}
    m = re.match(r"No preferred peg confirmed \((.*)\): stay in BTC$", txt)
    if m:
        bits = []
        for part in m.group(1).split(", "):
            pr, _, st = part.partition(" ")
            bits.append(fly_effect(pr, None if st == "n/a" else st, par.get(pr, "parent")))
        return "No flywheel ON (" + "; ".join(bits) + "): stay in BTC"
    m = re.match(r"(STRC|SATA) peg confirmed and (MSTR|ASST) (.*)$", txt)
    if m: return f"{m.group(1)} Flywheel ON \u2192 flywheel on for {m.group(2)}; {m.group(2)} {m.group(3)}"
    txt = txt.replace("Both pegs confirmed", "Both flywheels ON").replace("Peg on but parent overheated", "Flywheel ON but parent overheated")
    return re.sub(r"(STRC|SATA) peg is on but", r"\1 Flywheel ON but", txt)

# ---------------------------------------------------------------- recommendation
def recommendation(r, assets, cfg):
    """Peg confirmation (state ON) is the trigger; all-in (EV) mNAV overheated is a filter;
    rotation z only breaks a tie when both pegs are confirmed. Everything else -> STAY_BTC."""
    mc, rc = cfg["mnav"], cfg.get("recommendation", {})
    ob = rc.get("overheat_basis", "ev")
    info, cands = {}, []
    for A in assets:
        a, p = A["key"], A["preferred"]
        v, pc = r.get(f"{a}_mnav_{ob}"), r.get(f"{a}_mnav_{ob}_pct")
        oh = bool(v is not None and pc is not None and v >= mc["overheated_min_mnav"] and pc >= mc["overheated_percentile"])
        st = r.get("peg_" + p)
        info[A["ticker"]] = {"preferred": p, "peg_state": st, "peg_confirmed": st == "ON", "overheated": oh,
                             "flywheel_state": fly_label(st), "flywheel_effect": fly_effect(p, st, A["ticker"]),
                             "mnav_" + ob: v, "mnav_" + ob + "_pct": pc, "has_data": v is not None}
        if st == "ON" and not oh and v is not None: cands.append(A["ticker"])
    z = r.get("spread_z_" + ob, r.get("spread_z"))
    notes, other = [], None
    for t, x in info.items():
        if x["peg_confirmed"] and x["overheated"]:
            notes.append(f"{x['preferred']} Flywheel ON but {t} all-in mNAV is overheated: wait / reduce size")
    if len(cands) == 1:
        act, t = "BUY_" + cands[0], cands[0]
        reason = f"{info[t]['preferred']} Flywheel ON \u2192 flywheel on for {t}; {t} all-in mNAV {info[t]['mnav_'+ob]:.2f}x is not overheated"
    elif len(cands) >= 2:
        # spread = second - first (ASST - MSTR); z < 0 -> second relatively cheaper
        t0, t1 = assets[0]["ticker"], assets[1]["ticker"]
        pick = t1 if (z is not None and z < 0) else t0
        other = t0 if pick == t1 else t1
        act = "BUY_" + pick
        strength = "strong" if z is not None and (z <= mc["spread_z_favor_asst"] or z >= mc["spread_z_favor_mstr"]) else "weak"
        reason = (f"Both flywheels ON and neither parent overheated; rotation z {z:+.2f} ({strength} tilt) favors {pick}. "
                  f"{other} is also valid" if z is not None else f"Both flywheels ON; no rotation z yet, defaulting to {pick}. {other} is also valid")
    else:
        act = "STAY_BTC"
        on = [t for t, x in info.items() if x["peg_confirmed"]]
        if on and all(info[t]["overheated"] for t in on):
            reason = "Flywheel ON but parent overheated (all-in mNAV): wait / reduce size, stay in BTC"
        elif all(x["overheated"] for x in info.values()):
            reason = "Both parents overheated on all-in mNAV: stay in BTC"
        else:
            reason = "No flywheel ON (" + "; ".join(x["flywheel_effect"] for x in info.values()) + "): stay in BTC"
    return {"action": act, "reason": reason, "also_valid": other, "notes": notes, "rotation_z": z, "per_asset": info}

def rec_hash(rec):
    s = json.dumps({"a": rec["action"], "o": rec.get("also_valid"),
                    "p": {t: [x["peg_state"], x["overheated"]] for t, x in rec["per_asset"].items()}}, sort_keys=True)
    return hashlib.sha1(s.encode()).hexdigest()[:12]

def rec_hash_action(rec):
    return hashlib.sha1(rec["action"].encode()).hexdigest()[:12]

# ---------------------------------------------------------------- flywheel cards
def flywheel(rows, pref, parent, pc, extra_prices=None):
    R = [r for r in rows if r.get(pref) is not None]
    if not R: return {"preferred": pref, "parent": parent, "missing": True}
    last = R[-1]; streak = 0
    for r in reversed(R):
        if r[pref] >= pc["on_threshold"]: streak += 1
        else: break
    below = 0
    for r in reversed(R):
        if r[pref] < pc["on_threshold"]: below += 1
        else: break
    st = last.get("peg_" + pref)
    status = "FLYWHEEL_ON" if st == "ON" else ("NEAR_PAR" if st == "NEAR" else "OFF_PEG")
    return {"preferred": pref, "parent": parent, "date": last["date"], "close": last[pref], "peg_state": st, "status": status,
            "flywheel_state": fly_label(st), "flywheel_effect": fly_effect(pref, st, parent),
            "streak_days_at_or_above_threshold": streak, "days_below_threshold": below,
            "required_days": pc["on_consecutive_days"], "threshold": pc["on_threshold"],
            "distance_to_threshold": round(last[pref] - pc["on_threshold"], 3), "distance_to_par": round(last[pref] - pc["par"], 3)}

# ---------------------------------------------------------------- BTC yield
WINDOWS = (("30d", 30), ("90d", 90), ("ytd", None), ("1y", 365))

def bps_series(rows, a, basis):
    sk = a + ("_shares" if basis == "diluted" else "_basic_shares")
    return [(r["date"], r[a + "_btc"] / r[sk]) for r in rows if r.get(a + "_btc") and r.get(sk)]

def btc_yield(rows, assets, diluted_from=None):
    """diluted_from: {ticker: first date the diluted share count is a true diluted figure} (ASST: assumed
    fully diluted only from mid-Jul 2026). Windows starting before that use basic shares on both ends."""
    out = {}
    last_d = rows[-1]["date"]
    for A in assets:
        a, t = A["key"], A["ticker"]; df = (diluted_from or {}).get(t)
        res = {}
        ser = {"diluted": bps_series(rows, a, "diluted"), "basic": bps_series(rows, a, "basic")}
        def val(s, d):
            v = None
            for dd, x in s:
                if dd <= d: v = (dd, x)
                else: break
            return v
        def win(start, end=last_d):
            basis = "diluted" if (df is None or start >= df) else "basic"
            s = ser[basis]
            if not s or s[0][0] > start: return {"value": None, "basis": basis, "missing": f"history starts {s[0][0] if s else 'n/a'}"}
            a0, a1 = val(s, start), val(s, end)
            return {"value": round(a1[1] / a0[1] - 1, 5), "basis": basis, "from": a0[0], "to": a1[0]}
        for name, n in WINDOWS:
            start = (D(last_d).replace(month=1, day=1) - dt.timedelta(days=1)).isoformat() if n is None else _plus(last_d, -n)
            res[name] = win(start)
        res["prev_30d"] = win(_plus(last_d, -60), _plus(last_d, -30))
        c, p = res["30d"]["value"], res["prev_30d"]["value"]
        res["accelerating"] = bool(c is not None and p is not None and c > 0 and c > p)
        res["btc_per_1000_diluted_shares"] = round(ser["diluted"][-1][1] * 1000, 5) if ser["diluted"] else None
        out[t] = res
    return out

# ---------------------------------------------------------------- backtest
def backtest(rows, assets, start, idle="BTC"):
    """Recommendation of day t (known after the close) is executed at the close of day t+1, so it earns
    returns from t+1 -> t+2 onward. 100% in one asset (no leverage, no fees, no slippage)."""
    R = [r for r in rows if r["date"] >= start]
    px = lambda r, k: r.get("btc" if k == "BTC" else k)
    held, pos_series, eq = None, [], [1.0]
    rets, holds = [], []
    for k in range(len(R)):
        tgt = R[k - 2]["rec"]["action"] if k >= 2 else None
        if k >= 2:
            a = tgt.replace("BUY_", "") if tgt.startswith("BUY_") else ("BTC" if idle == "BTC" else "CASH")
            held = a
        holds.append(held)
    curves = {"rules": [1.0]}
    for k in range(1, len(R)):
        h = holds[k]
        r0, r1 = R[k - 1], R[k]
        if h in (None, "CASH"): g = 0.0
        else:
            p0, p1 = px(r0, h), px(r1, h)
            g = (p1 / p0 - 1) if (p0 and p1) else 0.0
        curves["rules"].append(curves["rules"][-1] * (1 + g))
    # holds[k] is the position over the interval (k-1, k]: target set at close k-2, executed at close k-1
    bench = {}
    for name in ["BTC"] + [A["ticker"] for A in assets]:
        c = [1.0]
        for k in range(1, len(R)):
            p0, p1 = px(R[k - 1], name), px(R[k], name)
            c.append(c[-1] * ((p1 / p0) if (p0 and p1) else 1.0))
        bench[name] = c
    def stats(c):
        peak, mdd = c[0], 0.0
        for v in c:
            peak = max(peak, v); mdd = min(mdd, v / peak - 1)
        return {"total_return": round(c[-1] - 1, 4), "max_drawdown": round(mdd, 4)}
    # holding periods
    periods, cur = [], None
    for k in range(1, len(R)):
        h = holds[k]
        if cur is None or h != cur["pos"]:
            if cur: periods.append(cur)
            cur = {"pos": h, "i0": k - 1, "i1": k}
        else: cur["i1"] = k
    if cur: periods.append(cur)
    out_p = []
    for p in periods:
        if p["pos"] is None: continue
        i0, i1 = p["i0"], p["i1"]
        rr = curves["rules"][i1] / curves["rules"][i0] - 1
        br = bench["BTC"][i1] / bench["BTC"][i0] - 1
        out_p.append({"position": p["pos"], "from": R[i0]["date"], "to": R[i1]["date"], "days": i1 - i0,
                      "return": round(rr, 4), "btc_return": round(br, 4), "beat_btc": (rr > br) if p["pos"] not in ("BTC", "CASH") else None})
    stock_p = [p for p in out_p if p["beat_btc"] is not None]
    switches = sum(1 for i in range(1, len(out_p)))
    days = {}
    for h in holds[1:]:
        days[h or "not yet invested"] = days.get(h or "not yet invested", 0) + 1
    n = len(holds) - 1
    return {"start": R[0]["date"], "end": R[-1]["date"], "trading_days": n, "idle": idle,
            "dates": [r["date"] for r in R], "curves": {k: [round(v, 5) for v in c] for k, c in {**curves, **bench}.items()},
            "positions": holds,
            "stats": {"rules": {**stats(curves["rules"]), "switches": switches,
                                "stock_periods": len(stock_p),
                                "hit_rate_vs_btc": round(sum(p["beat_btc"] for p in stock_p) / len(stock_p), 3) if stock_p else None,
                                "time_in": {k: round(v / n, 3) for k, v in days.items()}},
                      **{f"buy_hold_{k}": stats(c) for k, c in bench.items()}},
            "periods": out_p}

# ---------------------------------------------------------------- peg stretches
def peg_stretches(rows, pref, parent_key, parent, extra=(7, 30)):
    out, cur = [], None
    R = rows
    for i, r in enumerate(R):
        on = r.get("peg_" + pref) == "ON"
        if on and cur is None: cur = [i, i]
        elif on: cur[1] = i
        elif cur is not None: out.append(cur); cur = None
    if cur: out.append(cur)
    res = []
    bps = lambda r: (r[parent_key + "_btc"] / r[parent_key + "_shares"]) if r.get(parent_key + "_btc") and r.get(parent_key + "_shares") else None
    for i0, i1 in out:
        a, b = R[i0], R[i1]
        ongoing = i1 == len(R) - 1
        # 'during': from the first confirmed close to the last confirmed close (next close if the stretch is 1 day)
        e = R[min(i1 + 1, len(R) - 1)] if i1 == i0 else b
        row = {"preferred": pref, "parent": parent, "confirmed": a["date"], "last_on": b["date"], "days_on": i1 - i0 + 1, "ongoing": ongoing,
               "during": {"parent": _ch(a.get(parent), e.get(parent)), "btc": _ch(a["btc"], e["btc"]), "btc_per_share": _ch(bps(a), bps(e)), "to": e["date"]}}
        for n in extra:
            f = _at_or_after(R, _plus(a["date"], n), parent)
            row[f"after_{n}d"] = None if (f is None or f["date"] > _plus(a["date"], n + 6)) else \
                {"parent": _ch(a.get(parent), f.get(parent)), "btc": _ch(a["btc"], f["btc"]), "btc_per_share": _ch(bps(a), bps(f)), "to": f["date"]}
        res.append(row)
    return res

def _ch(x, y): return round(y / x - 1, 4) if (x and y) else None

# ---------------------------------------------------------------- ATM / issuance summary
def atm_summary(fuel, prices, pc, sats_points, today):
    t = today
    def wsum(items, key, days=None, ytd=False):
        s0 = f"{t[:4]}-01-01" if ytd else _plus(t, -days)
        sel = [x for x in items if x["end"] >= s0]
        return round(sum((x.get(key) or 0) for x in sel), 1), len(sel)
    atm, rep = fuel.get("strc_atm", []), fuel.get("strc_repurchase", [])
    first = min([x["start"] for x in atm] or [None]) if atm else None
    strc = {"coverage_from": first, "recent_weeks": [dict(x, repurchased_m=next((y["usd_m"] for y in rep if y["end"] == x["end"]), 0)) for x in atm[-6:]][::-1],
            "totals": {}}
    for name, days, ytd in (("30d", 30, False), ("90d", 90, False), ("ytd", None, True), ("1y", 365, False)):
        net, n = wsum(atm, "net_m", days, ytd); notl, _ = wsum(atm, "notional_m", days, ytd); sh, _ = wsum(atm, "shares", days, ytd)
        rp, _ = wsum(rep, "usd_m", days, ytd)
        s0 = f"{t[:4]}-01-01" if ytd else _plus(t, -days)
        strc["totals"][name] = {"atm_net_proceeds_m": net, "atm_notional_m": notl, "atm_shares": sh, "repurchased_m": rp,
                                "net_issuance_m": round(net - rp, 1), "weeks": n, "partial": bool(first and first > s0)}
    # SATA: change in shares outstanding x $100 stated amount (proceeds not parsed; may include exchanges)
    so = fuel.get("sata_outstanding", [])
    chg = [{"date": b["date"], "prev_date": a["date"], "shares_added": b["shares"] - a["shares"],
            "stated_value_m": round((b["shares"] - a["shares"]) * 100 / 1e6, 1)} for a, b in zip(so, so[1:]) if b["shares"] != a["shares"]]
    def lvl(d):
        v = None
        for x in so:
            if x["date"] <= d: v = x
        return v
    sata = {"coverage_from": so[0]["date"] if so else None, "latest": so[-1] if so else None, "recent_changes": chg[-6:][::-1], "totals": {}}
    for name, days, ytd in (("30d", 30, False), ("90d", 90, False), ("ytd", None, True), ("1y", 365, False)):
        s0 = f"{int(t[:4]) - 1}-12-31" if ytd else _plus(t, -days)
        a0 = lvl(s0)
        if not a0 or not so: sata["totals"][name] = {"shares_added": None, "missing": "no SATA count at window start"}; continue
        d_sh = so[-1]["shares"] - a0["shares"]
        sata["totals"][name] = {"shares_added": d_sh, "stated_value_m": round(d_sh * 100 / 1e6, 1), "from": a0["date"]}
    sats = {"points": sats_points, "latest_shares": sats_points[-1][1] if sats_points else None,
            "stated_value_m": round(sats_points[-1][1] * 100 / 1e6, 1) if sats_points else None}
    # days at / near par
    peg = {}
    for p in ("STRC", "SATA", "SATS"):
        ser = sorted(prices.get(p, {}).items())
        if not ser: peg[p] = {"missing": True}; continue
        res = {"listed_data_from": ser[0][0]}
        for name, days in (("30d", 30), ("90d", 90), ("1y", 365)):
            s0 = _plus(t, -days); sel = [c for d, c in ser if d > s0]
            n = len(sel)
            res[name] = {"days": n, "partial": ser[0][0] > s0,
                         "at_or_above_par": round(sum(c >= pc["par"] for c in sel) / n, 3) if n else None,
                         "near_par": round(sum(c >= pc["watch_below"] for c in sel) / n, 3) if n else None,
                         "at_or_above_on_threshold": round(sum(c >= pc["on_threshold"] for c in sel) / n, 3) if n else None,
                         "within_1_dollar": round(sum(c >= pc["sell_below"] for c in sel) / n, 3) if n else None}
        peg[p] = res
    return {"STRC": strc, "SATA": sata, "SATS": sats, "peg_days": peg,
            "notes": ["STRC: weekly ATM sales and repurchases parsed from Strategy weekly 8-Ks (coverage from the first parsed week). Net proceeds as reported.",
                      "SATA: Strive's weekly tables give shares outstanding, not ATM proceeds, so issuance is shown as the change in shares x $100 stated amount. It is not cash raised (SATA traded below $100 at times), and some steps are exchanges (e.g. Jan 2026 notes exchange).",
                      "SATS: IPO settled 2026-10-20; count from Strive's 2026-10-26 weekly 8-K; shown at the $100 stated amount.",
                      "Near par = close >= peg.watch_below; on threshold = close >= peg.on_threshold; windows are calendar days, share of trading-day closes."]}

# ---------------------------------------------------------------- signal flips + log
def _moves(rows, d, assets, basis):
    base = _at_or_after(rows, d)
    out = {}
    for n in (1, 7, 30):
        f = _at_or_after(rows, _plus(d, n))
        if not f or not base or f["date"] == base["date"]: out[f"{n}d"] = None; continue
        m = {"to": f["date"], "BTC": _ch(base["btc"], f["btc"])}
        for A in assets:
            t, a = A["ticker"], A["key"]
            m[t] = _ch(base.get(t), f.get(t))
            m[t + "_mnav_" + basis] = _ch(base.get(f"{a}_mnav_{basis}"), f.get(f"{a}_mnav_{basis}"))
        out[f"{n}d"] = m
    return out

def signal_flips(rows, assets, start, basis="ev"):
    R = [r for r in rows if r["date"] >= start]
    flips = []
    for p, r in zip(R, R[1:]):
        if p["rec"]["action"] != r["rec"]["action"]:
            flips.append({"date": r["date"], "old": p["rec"]["action"], "new": r["rec"]["action"], "reason": r["rec"]["reason"],
                          "after": _moves(rows, r["date"], assets, basis), "reconstructed": True})
    return flips

def update_signal_log(path, rec, as_of, gen_utc, rows, assets, basis="ev"):
    log = json.loads(path.read_text()) if path.exists() else {"_comment": "Appended by the daily job when the recommendation changes. 'after' moves are recomputed each run.", "entries": []}
    E = log["entries"]; h = rec_hash(rec)
    if not E or E[-1]["new_hash"] != h:
        E.append({"detected_utc": gen_utc, "as_of": as_of, "old": E[-1]["new"] if E else None, "new": rec["action"],
                  "old_hash": E[-1]["new_hash"] if E else None, "new_hash": h, "reason": rec["reason"], "initial": not E})
    for e in E: e["reason"] = relabel_reason(e.get("reason"))
    for e in E: e["after"] = _moves(rows, e["as_of"], assets, basis)
    path.write_text(json.dumps(log, indent=1))
    return log


def buy_flags(last, assets, cfg):
    """Per-asset display flags: Flywheel + Cheap, and very-low all-in mNAV (secondary cue for the parent)."""
    bf = cfg.get("buy_flags", {}); bs = bf.get("basis", "ev")
    lf = bf.get("low_fair_max_percentile", 0.5); vm = bf.get("very_low_max_mnav", 1.0); vp = bf.get("very_low_max_percentile", 0.2)
    out = {}
    for A in assets:
        k = A["key"]; m = last.get(f"{k}_mnav_{bs}"); p = last.get(f"{k}_mnav_{bs}_pct"); z = last.get(f"{k}_zone_{bs}")
        st = last.get("peg_" + A["preferred"])
        low_fair = z == "FAIR" and p is not None and p < lf
        cheapish = z == "CHEAP" or low_fair
        vlow = m is not None and (m <= vm or (p is not None and p <= vp))
        why = []
        if m is not None and m <= vm: why.append(f"all-in mNAV {m:.2f}x <= {vm}x")
        if p is not None and p <= vp: why.append(f"1y percentile {p:.0%} <= {vp:.0%}")
        out[A["ticker"]] = {"preferred": A["preferred"], "peg_state": st, "flywheel_state": fly_label(st), "mnav": m, "mnav_pct_1y": p, "zone": z,
                            "low_fair": low_fair, "cheap_or_low_fair": cheapish,
                            "flywheel_cheap": st == "ON" and cheapish,
                            "very_low_mnav": vlow, "very_low_reason": "; ".join(why) or None,
                            "very_low_with_peg_near_or_on": vlow and st in ("ON", "NEAR")}
    return {"basis": bs, "low_fair_max_percentile": lf, "very_low_max_mnav": vm, "very_low_max_percentile": vp, "per_asset": out}
