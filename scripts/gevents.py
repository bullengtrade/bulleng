"""
BullEng - US and UK dividend calendar and earnings tracker (via yfinance).

For each tracked US and UK stock:
  - next ex-dividend and payment dates, last dividend, 12-month dividend and yield
  - last reported earnings (EPS vs analyst estimate) and the next earnings date

Writes data/us/events.json and data/uk/events.json
"""

import datetime as dt
import json
import math
import pathlib
import time

import yfinance as yf


def d2s(v):
    if v is None:
        return None
    try:
        if isinstance(v, (list, tuple)):
            v = v[0] if v else None
        if v is None:
            return None
        if hasattr(v, "date") and callable(v.date):
            v = v.date()
        return v.isoformat()[:10]
    except Exception:
        return None


def num(v, nd=4):
    try:
        v = float(v)
        return None if math.isnan(v) or math.isinf(v) else round(v, nd)
    except (TypeError, ValueError):
        return None


def run(market):
    site = json.loads(pathlib.Path(f"data/{market}/site.json").read_text())
    today = dt.date.today()
    divs, earns, problems = [], [], 0
    for s in site["stocks"]:
        sym, full, price = s["sym"], s["full"], s.get("price")
        try:
            t = yf.Ticker(full)
        except Exception:
            problems += 1
            continue

        # ---- dividends ----
        try:
            cal = t.calendar or {}
        except Exception:
            cal = {}
        try:
            hist = t.dividends
            hist = hist[hist.index >= hist.index.max() - dt.timedelta(days=365)] if len(hist) else hist
            last_amt = num(hist.iloc[-1]) if len(hist) else None
            last_date = d2s(hist.index[-1]) if len(hist) else None
            ttm = num(hist.sum()) if len(hist) else None
        except Exception:
            last_amt = last_date = ttm = None
        if market == "uk" and ttm and price:
            # London dividends are sometimes given in pounds while prices are in pence
            if ttm / price < 0.0015 and ttm * 100 / price < 0.15:
                ttm, last_amt = ttm * 100, (last_amt or 0) * 100
        ex = d2s(cal.get("Ex-Dividend Date"))
        pay = d2s(cal.get("Dividend Date"))
        if ttm or ex:
            divs.append({"sym": sym, "name": s["name"], "ex_date": ex, "pay_date": pay,
                         "last_amount": last_amt, "last_date": last_date, "ttm": ttm,
                         "yield": num(ttm / price * 100, 2) if ttm and price else None})

        # ---- earnings ----
        nxt = d2s(cal.get("Earnings Date"))
        last = None
        try:
            ed = t.get_earnings_dates(limit=8)
            if ed is not None and len(ed):
                past = ed[ed["Reported EPS"].notna()] if "Reported EPS" in ed.columns else ed.iloc[0:0]
                if len(past):
                    r = past.iloc[0]
                    last = {"date": d2s(past.index[0]), "eps": num(r.get("Reported EPS")),
                            "estimate": num(r.get("EPS Estimate")), "surprise": num(r.get("Surprise(%)"), 1)}
                fut = ed[ed.index.tz_localize(None) >= dt.datetime.now()] if hasattr(ed.index, "tz_localize") else ed.iloc[0:0]
                if len(fut) and not nxt:
                    nxt = d2s(fut.index[-1])
        except Exception:
            pass
        if nxt or last:
            earns.append({"sym": sym, "name": s["name"], "next": nxt, "last": last})
        time.sleep(0.3)

    out = {"updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
           "dividends": divs, "earnings": earns}
    pathlib.Path(f"data/{market}/events.json").write_text(json.dumps(out, separators=(",", ":")))
    up_div = [d for d in divs if (d["ex_date"] or "") >= today.isoformat()]
    up_earn = [e for e in earns if (e["next"] or "") >= today.isoformat()]
    print(f"{market.upper()}: dividends for {len(divs)} stocks ({len(up_div)} with upcoming ex-dates), "
          f"earnings for {len(earns)} ({len(up_earn)} upcoming dates), {problems} problems")
    if divs:
        d = divs[0]
        print(f"  sample: {d['sym']} ex {d['ex_date']} pay {d['pay_date']} last {d['last_amount']} yield {d['yield']}%")


def main():
    for m in ("us", "uk"):
        try:
            run(m)
        except Exception as e:
            print(f"{m.upper()} events failed: {e}")


if __name__ == "__main__":
    main()
