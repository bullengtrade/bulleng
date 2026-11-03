"""
BullEng - US put/call ratios (end-of-day, via yfinance).

For the 100 most traded S&P 500 stocks plus SPY, QQQ and IWM, adds up put and
call volume and open interest over the nearest three expiry dates.

  put/call volume ratio  = put contracts traded today / call contracts traded today
  put/call OI ratio      = open put contracts / open call contracts

Keeps a daily history so each ratio can be compared with that stock's own
normal level. Writes data/us/putcall.json and data/us/putcall_hist.json.
"""

import datetime as dt
import json
import math
import pathlib
import time

import yfinance as yf

TOP_N = 100
INDEX_ETFS = [("SPY", "S&P 500 ETF"), ("QQQ", "Nasdaq-100 ETF"), ("IWM", "Russell 2000 ETF")]
EXPIRIES = 3
OUT = pathlib.Path("data/us/putcall.json")
HIST = pathlib.Path("data/us/putcall_hist.json")


def tot(df, col):
    try:
        return float(df[col].fillna(0).sum())
    except Exception:
        return 0.0


def ratio(p, c):
    return round(p / c, 3) if c > 0 else None


def pct_rank(value, history):
    vals = [h for h in history if h is not None]
    if value is None or len(vals) < 20:
        return None
    return round(sum(v <= value for v in vals) / len(vals) * 100)


def main():
    site = json.loads(pathlib.Path("data/us/site.json").read_text())
    top = sorted(site["stocks"], key=lambda s: -(s.get("turnover") or 0))[:TOP_N]
    names = INDEX_ETFS + [(s["full"], s["name"]) for s in top]
    hist = json.loads(HIST.read_text()) if HIST.exists() else {}
    today = dt.date.today().isoformat()
    out, failed = {}, 0

    for sym, name in names:
        try:
            t = yf.Ticker(sym)
            exps = list(t.options or [])[:EXPIRIES]
            if not exps:
                failed += 1
                continue
            cv = pv = coi = poi = 0.0
            for e in exps:
                ch = t.option_chain(e)
                cv += tot(ch.calls, "volume"); pv += tot(ch.puts, "volume")
                coi += tot(ch.calls, "openInterest"); poi += tot(ch.puts, "openInterest")
                time.sleep(0.25)
        except Exception:
            failed += 1
            continue
        if cv + pv < 100:            # too little trading to mean anything
            continue
        key = sym
        h = [x for x in hist.get(key, []) if x[0] != today] + [[today, ratio(pv, cv), ratio(poi, coi)]]
        hist[key] = h[-250:]
        out[key] = {"name": name, "pcr_vol": ratio(pv, cv), "pcr_oi": ratio(poi, coi),
                    "call_vol": int(cv), "put_vol": int(pv), "call_oi": int(coi), "put_oi": int(poi),
                    "expiries": exps, "days": len(hist[key]),
                    "vol_rank": pct_rank(ratio(pv, cv), [x[1] for x in hist[key]]),
                    "oi_rank": pct_rank(ratio(poi, coi), [x[2] for x in hist[key]])}

    # market-wide figure across all tracked single stocks (not the ETFs)
    stocks = [v for k, v in out.items() if k not in dict(INDEX_ETFS)]
    cv, pv = sum(v["call_vol"] for v in stocks), sum(v["put_vol"] for v in stocks)
    mk = {"pcr_vol": ratio(pv, cv), "call_vol": int(cv), "put_vol": int(pv), "n": len(stocks)}
    h = [x for x in hist.get("_ALL", []) if x[0] != today] + [[today, mk["pcr_vol"], None]]
    hist["_ALL"] = h[-250:]
    mk["days"], mk["vol_rank"] = len(hist["_ALL"]), pct_rank(mk["pcr_vol"], [x[1] for x in hist["_ALL"]])

    OUT.write_text(json.dumps({"updated": today, "market": mk, "stocks": out}, separators=(",", ":")))
    HIST.write_text(json.dumps(hist, separators=(",", ":")))
    spy = out.get("SPY", {})
    print(f"Put/call ratios for {len(out)} tickers ({failed} without options data). "
          f"SPY volume ratio {spy.get('pcr_vol')}, all stocks {mk['pcr_vol']}")


if __name__ == "__main__":
    main()
