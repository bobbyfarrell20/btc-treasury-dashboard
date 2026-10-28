"""Flywheel state engine for a $100-par preferred (STRC -> MSTR, SATA -> ASST). Rule as of 2026-10-28 (review applied).

Ladder (codes in rows["fly_<PREF>"] / flywheel_code):
  RUNNING_HOT  on,  latest price >= running_hot.price_min ($100) AND heavy volume (see below)
  ON           on   ("FLYWHEEL ON")
  WARMING_UP   off for the call: touched >= on_trade_threshold ($99.80) without a qualifying CLOSE (watch only)
  COOLING_OFF  off, latest price >= stalled_below ($99)
  STALLED      off, latest price <  $99
Turning ON needs a completed daily CLOSE >= on_close_threshold[pref] (STRC $100.00, SATA $99.80). An intraday touch never
  turns it on; before today's 4 PM ET close the call is based on the last completed close (plus the intraday off rule).
Turning OFF (unchanged): once ON it stays ON until it trades below on_trade_threshold ($99.80) for MORE THAN
  off_minutes_below (30) consecutive regular-session minutes (overnight / weekend gaps never add minutes).
WARMING_UP (off): the day's high >= $99.80 (STRC: also any close $99.80-$99.99) and, with intraday bars, the price has
  not since spent > off_minutes_below minutes below $99.80.
Data: Yahoo 1m bars (~last 7 days) where available, else 5m bars (~last 60 days); a bar counts as "below" when its
  high < $99.80; the below-run grows by the time between bars.
DAILY-ONLY APPROXIMATION (older history without intraday bars): ON at a close >= the close threshold; an ON flywheel turns
  OFF when the day's close < $99.80; a day whose high touched $99.80 but whose close is below the close threshold is
  WARMING_UP (off) unless the flywheel was already on.
Heavy volume (RUNNING_HOT): volume >= running_hot.vol_mult_min (1.5x) the prior vol_avg_days (20) trading-day average;
  for today's unfinished session the pace = volume so far / fraction of the 390-minute session elapsed; when intraday
  bars exist, >= min_share_at_or_above (50%) of that day's volume must have traded at >= $100 (bar close).
Legacy peg_state codes (used by the call and the signal hash): RUNNING_HOT / ON -> ON, WARMING_UP / COOLING_OFF -> WATCH,
  STALLED -> SELL. So RUNNING_HOT <-> ON never moves the hash; WARMING_UP <-> ON does.
"""
import datetime as dt
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ON_CODES = ("RUNNING_HOT", "ON")          # on for the call
LEGACY = {"RUNNING_HOT": "ON", "ON": "ON", "WARMING_UP": "WATCH", "COOLING_OFF": "WATCH", "STALLED": "SELL", None: None}
LABEL = {"RUNNING_HOT": "RUNNING HOT", "ON": "FLYWHEEL ON", "WARMING_UP": "WARMING UP", "COOLING_OFF": "COOLING OFF",
         "STALLED": "STALLED", None: "NO DATA"}

def _iso(ts): return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def _close_ts(d):
    D = dt.date.fromisoformat(d)
    return dt.datetime(D.year, D.month, D.day, 16, 0, tzinfo=ET).timestamp()

def on_close_threshold(pc, pref):
    v = pc.get("on_close_threshold")
    if isinstance(v, dict): return v.get(pref, pc["on_trade_threshold"])
    return v if v is not None else pc["on_trade_threshold"]

def compute(dates, daily, bars, pc, now=None, pref=None):
    """dates: row dates. daily: {date: {o,h,l,c,v}}. bars: {date: [(ts, minutes, h, l, c, v), ...]} regular session only.
    Returns {date: info}."""
    T, OFFM, STALL = pc["on_trade_threshold"], pc["off_minutes_below"], pc["stalled_below"]
    TC = on_close_threshold(pc, pref)
    rh = pc.get("running_hot", {}); HOT, VM, VN, SH = rh.get("price_min", 100.0), rh.get("vol_mult_min", 1.5), rh.get("vol_avg_days", 20), rh.get("min_share_at_or_above", 0.5)
    now = now or dt.datetime.now(dt.timezone.utc)
    now_et = now.astimezone(ET); today = now_et.date().isoformat()
    after_close = now_et.time() >= dt.time(16, 0)
    on, on_since, below_min, below_start, last_below, off_since = False, None, 0.0, None, None, None
    last_close_on = None    # last completed session's close used for the call
    prev_ts = None
    out, vols = {}, []
    for d in dates:
        x = daily.get(d)
        if x is None or x.get("c") is None:
            out[d] = None; continue
        done = d < today or (d == today and after_close)
        B = bars.get(d)
        touched = False
        if B:
            method = "intraday_1m" if B[0][1] == 1 else "intraday_5m"
            pday = None
            prev_below = below_min > 0          # a below-run can carry over from the previous session
            for ts, mins, h, l, c, v in B:
                if h is None: continue
                step = max(mins, (ts - prev_ts) / 60.0) if (pday == d and prev_below) else mins
                pday = d; prev_ts = ts; prev_below = round(h, 4) < T
                if l is not None and round(l, 4) < T: last_below = ts
                if round(h, 4) >= T:
                    touched = True
                    below_min, below_start = 0.0, None
                else:
                    below_min += step
                    if below_start is None: below_start = ts
                    if on and below_min > OFFM:
                        on, off_since = False, ts
            px = B[-1][4] if (d == today and not done and B[-1][4] is not None) else x["c"]
            warm_ok = below_min <= OFFM
        else:
            method = "daily"
            prev_ts = None
            if x.get("h") is not None and x["h"] >= T: touched = True
            if done and on and x["c"] < T:
                on, off_since = False, d
            if x.get("l") is not None and x["l"] < T: last_below = d
            px = x["c"]
            warm_ok = True
        if done:
            if x["c"] >= TC:
                if not on: on, on_since, off_since = True, (_close_ts(d) if B else d), None
                below_min, below_start = 0.0, None
            last_close_on = x["c"]
        # volume vs prior N-day average; pace-adjust an unfinished session
        v = x.get("v") or 0
        avg = sum(vols[-VN:]) / len(vols[-VN:]) if len(vols) >= min(5, VN) else None
        frac = 1.0
        if d == today:
            o = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
            frac = min(1.0, max(0.05, (now_et - o).total_seconds() / 60 / 390))
        pace = v / frac if frac else v
        mult = round(pace / avg, 2) if avg else None
        share = None
        if B:
            tv = sum((b[5] or 0) for b in B)
            if tv: share = round(sum((b[5] or 0) for b in B if b[4] is not None and b[4] >= HOT) / tv, 3)
        heavy = mult is not None and mult >= VM and (share is None or share >= SH)
        if on:
            code = "RUNNING_HOT" if (px >= HOT and heavy) else "ON"
        elif touched and warm_ok:
            code = "WARMING_UP"
        else:
            code = "COOLING_OFF" if px >= STALL else "STALLED"
        out[d] = {"code": code, "on": on, "price": round(px, 4), "method": method, "session_complete": done,
                  "on_close_threshold": TC, "touched_trigger": touched,
                  "on_since": (_iso(on_since) if isinstance(on_since, (int, float)) else on_since) if on else None,
                  "off_since": (_iso(off_since) if isinstance(off_since, (int, float)) else off_since) if not on else None,
                  "last_below_trigger": (_iso(last_below) if isinstance(last_below, (int, float)) else last_below),
                  "minutes_below_now": round(below_min, 1), "vol_mult": mult, "vol_partial_session": d == today and frac < 1,
                  "share_vol_at_or_above_100": share}
        if d != today or frac >= 1: vols.append(v)
    return out

def fetch_bars(get_json, ticker):
    """{date: [(ts, minutes, h, l, c, v)]}: 1m bars where available, else 5m (regular session only)."""
    res = {}
    for interval, rng, mins in (("5m", "60d", 5), ("1m", "7d", 1)):
        try:
            j = get_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range={rng}&interval={interval}&includePrePost=false")
            r = j["chart"]["result"][0]; q = r["indicators"]["quote"][0]
        except Exception:
            continue
        per = {}
        for i, ts in enumerate(r.get("timestamp") or []):
            t_et = dt.datetime.fromtimestamp(ts, ET)
            if not (dt.time(9, 30) <= t_et.time() < dt.time(16, 0)): continue
            h, l, c, v = q["high"][i], q["low"][i], q["close"][i], q["volume"][i]
            if h is None: continue
            per.setdefault(t_et.date().isoformat(), []).append((ts, mins, h, l, c, v))
        for d, B in per.items(): res[d] = B   # 1m pass runs second and overrides 5m days it covers
    return res
