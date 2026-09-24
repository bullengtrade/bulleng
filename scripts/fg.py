"""
BullEng - Fear & Greed indices (0 = extreme fear, 100 = extreme greed).

US and UK (two years of prices via yfinance), each signal scored 0-100 by
where today's value sits in its own last-year range (percentile):
  momentum   - index vs its 50-day average
  strength   - index position within its 52-week high/low range
  breadth    - share of BullEng's tracked stocks above their 50-day average
  volatility - VIX vs its 50-day average (US) / 20-day realised volatility (UK), inverted
  safe haven - stocks vs government bonds over 20 days

CSE (BullEng's own daily archive + tagged news), fixed scales:
  breadth, foreign flows, news sentiment        - available from day one
  momentum, volatility                           - once 20 trading days are recorded

Writes data/fg.json
"""

import datetime as dt
import json
import math
import pathlib
import statistics as st

import yfinance as yf

OUT = pathlib.Path("data/fg.json")
SL = dt.timezone(dt.timedelta(hours=5, minutes=30))


def label(v):
    return ("Extreme fear" if v < 25 else "Fear" if v < 45 else "Neutral" if v <= 55
            else "Greed" if v <= 75 else "Extreme greed")


def clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def lin(x, bad, good):
    """Map x linearly: bad -> 0, good -> 100."""
    return clamp((x - bad) / (good - bad) * 100)


def pct_rank(series, invert=False):
    """Percentile of each value within the previous 252 values (0-100)."""
    out = []
    for i in range(len(series)):
        w = [v for v in series[max(0, i - 251):i + 1] if v is not None and not math.isnan(v)]
        v = series[i]
        if v is None or math.isnan(v) or len(w) < 60:
            out.append(None)
            continue
        p = sum(x <= v for x in w) / len(w) * 100
        out.append(100 - p if invert else p)
    return out


def close(df, t):
    try:
        s = df[t]["Close"] if (t, "Close") in df.columns else df["Close"]
        return s.ffill()
    except Exception:
        return None


def market(key, index, vol, stock_bench, bond, stocks):
    tickers = [index, stock_bench, bond] + ([vol] if vol else []) + stocks
    df = yf.download(tickers, period="2y", interval="1d", group_by="ticker", auto_adjust=True,
                     threads=True, progress=False)
    idx = close(df, index).dropna()
    dates = idx.index
    comp = {}

    ma50 = idx.rolling(50).mean()
    comp["Momentum"] = (pct_rank(list((idx / ma50 - 1).values)), "Index compared with its 50-day average")
    hi, lo = idx.rolling(252, min_periods=120).max(), idx.rolling(252, min_periods=120).min()
    comp["Strength"] = ([None if (h - l) == 0 or math.isnan(h) else clamp((v - l) / (h - l) * 100)
                         for v, h, l in zip(idx.values, hi.values, lo.values)],
                        "Where the index sits between its 52-week low and high")
    above = []
    for t in stocks:
        c = close(df, t)
        if c is not None and c.notna().sum() > 60:
            above.append((c > c.rolling(50).mean()).reindex(dates).astype(float))
    if above:
        share = sum(above) / len(above) * 100
        comp["Breadth"] = (pct_rank(list(share.values)), "Share of tracked stocks above their 50-day average")
    if vol:
        v = close(df, vol).reindex(dates).ffill()
        comp["Volatility"] = (pct_rank(list((v / v.rolling(50).mean()).values), invert=True),
                              "VIX fear gauge compared with its 50-day average (high VIX = fear)")
    else:
        rv = idx.pct_change().rolling(20).std()
        comp["Volatility"] = (pct_rank(list(rv.values), invert=True), "20-day price swings (bigger swings = fear)")
    s, b = close(df, stock_bench).reindex(dates).ffill(), close(df, bond).reindex(dates).ffill()
    comp["Safe haven"] = (pct_rank(list((s.pct_change(20) - b.pct_change(20)).values)),
                          "Shares versus government bonds over 20 days")

    history = []
    for i, d in enumerate(dates):
        vals = [c[0][i] for c in comp.values() if c[0][i] is not None]
        if len(vals) >= 3:
            history.append([d.date().isoformat(), round(sum(vals) / len(vals))])
    if not history:
        return None
    last = len(dates) - 1
    comps = [{"name": n, "score": round(c[0][last]), "detail": c[1]} for n, c in comp.items() if c[0][last] is not None]
    score = history[-1][1]
    print(f"{key.upper()} Fear & Greed {score} ({label(score)}): " + ", ".join(f"{c['name']} {c['score']}" for c in comps))
    return {"score": score, "label": label(score), "date": history[-1][0], "components": comps,
            "history": history[-90:], "partial": False}


def cse():
    raw = sorted(pathlib.Path("data/lk/raw").glob("*.json"))
    days = {}
    for f in raw:
        try:
            r = json.loads(f.read_text())
            td = dt.datetime.fromtimestamp(r["aspiData"]["timestamp"] / 1000, SL).date().isoformat()
            trades = (r.get("tradeSummary") or {}).get("reqTradeSummery") or []
            sh = [t for t in trades if (t.get("symbol") or "").split(".")[-1][:1] in ("N", "X") and (t.get("tradevolume") or 0) > 0]
            up = sum((t.get("percentageChange") or 0) > 0 for t in sh)
            dn = sum((t.get("percentageChange") or 0) < 0 for t in sh)
            dms = (r.get("dailyMarketSummery") or [[{}]])[0][0]
            fnet = (dms.get("equityForeignPurchase") or 0) - (dms.get("equityForeignSales") or 0)
            days[td] = {"aspi": r["aspiData"]["value"], "up": up, "down": dn, "fnet": fnet,
                        "turnover": dms.get("equityTurnover") or 0}
        except Exception:
            continue
    seq = [days[k] for k in sorted(days)]
    if not seq:
        return None
    comps = []
    last5 = seq[-5:]
    u, d = sum(x["up"] for x in last5), sum(x["down"] for x in last5)
    if u + d:
        comps.append({"name": "Breadth", "score": round(lin(u / (u + d), 0.3, 0.7)),
                      "detail": f"{u / (u + d) * 100:.0f}% of moving shares rose over the last {len(last5)} sessions"})
    tv = sum(x["turnover"] for x in last5)
    if tv:
        fr = sum(x["fnet"] for x in last5) / tv
        comps.append({"name": "Foreign flows", "score": round(lin(fr, -0.15, 0.15)),
                      "detail": f"Foreign investors were net {'buyers' if fr >= 0 else 'sellers'} of {abs(fr) * 100:.1f}% of turnover"})
    try:
        items = json.loads(pathlib.Path("data/news/lk/tagged.json").read_text())["items"]
        cut = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)).isoformat()
        rec = [i for i in items if i.get("published", "") >= cut[:16]]
        pos, neg = sum(i.get("impact") == "pos" for i in rec), sum(i.get("impact") == "neg" for i in rec)
        if pos + neg >= 3:
            comps.append({"name": "News sentiment", "score": round(lin(pos / (pos + neg), 0.25, 0.75)),
                          "detail": f"{pos} likely-positive vs {neg} likely-negative economic stories in the past week"})
    except Exception:
        pass
    aspi = [x["aspi"] for x in seq]
    if len(aspi) >= 20:
        ma = sum(aspi[-20:]) / 20
        comps.append({"name": "Momentum", "score": round(lin(aspi[-1] / ma - 1, -0.05, 0.05)),
                      "detail": "ASPI compared with its 20-day average"})
        rets = [aspi[i] / aspi[i - 1] - 1 for i in range(len(aspi) - 19, len(aspi))]
        vol = st.pstdev(rets) * math.sqrt(250)
        comps.append({"name": "Volatility", "score": round(lin(vol, 0.30, 0.10)),
                      "detail": f"ASPI price swings of {vol * 100:.0f}% a year (bigger swings = fear)"})
    if len(comps) < 2:
        return None
    score = round(sum(c["score"] for c in comps) / len(comps))
    hist_f = pathlib.Path("data/lk/fg_history.json")
    hist = json.loads(hist_f.read_text()) if hist_f.exists() else []
    today = sorted(days)[-1]
    hist = [h for h in hist if h[0] != today] + [[today, score]]
    hist_f.write_text(json.dumps(hist[-250:]))
    print(f"CSE Fear & Greed {score} ({label(score)}): " + ", ".join(f"{c['name']} {c['score']}" for c in comps)
          + f" - {len(seq)} sessions recorded")
    return {"score": score, "label": label(score), "date": today, "components": comps,
            "history": hist[-90:], "partial": len(aspi) < 20, "sessions": len(seq)}


def main():
    out = {"updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")}
    us_stocks = [s["full"] for s in json.loads(pathlib.Path("data/us/site.json").read_text())["stocks"]]
    uk_stocks = [s["full"] for s in json.loads(pathlib.Path("data/uk/site.json").read_text())["stocks"]]
    for key, fn in (("us", lambda: market("us", "^GSPC", "^VIX", "SPY", "TLT", us_stocks)),
                    ("uk", lambda: market("uk", "^FTSE", None, "ISF.L", "IGLT.L", uk_stocks)),
                    ("lk", cse)):
        try:
            r = fn()
            if r:
                out[key] = r
        except Exception as e:
            print(f"{key.upper()} Fear & Greed failed: {e}")
    OUT.write_text(json.dumps(out, separators=(",", ":")))


if __name__ == "__main__":
    main()
