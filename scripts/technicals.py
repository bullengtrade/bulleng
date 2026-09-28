"""
BullEng - technical snapshot and "what happened next, historically".

For every US and UK stock and the major cryptoassets (up to 10 years of daily
prices via yfinance):

  snapshot  - price vs 50/200-day averages, golden/death cross, RSI(14), MACD,
              52-week position, strength vs the index, recent support/resistance
  outcomes  - for 5 days, 1 month, 3 months and 1 year: what the price did next
              on past days whose setup (trend vs 200-day average + RSI zone)
              matched today's, compared with all days. 5 years: the stock's
              actual past 5-year returns.

Writes data/us/tech.json, data/uk/tech.json, data/crypto/tech.json.
This is history, not a forecast.
"""

import datetime as dt
import json
import math
import pathlib

import numpy as np
import pandas as pd
import yfinance as yf

STABLE = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "PYUSD", "USDS", "USD1", "BUSD", "USDD", "GUSD"}
SPACING = 5          # keep past samples at least 5 days apart (less overlap)
MIN_N = 20           # fewer samples than this = "not enough history"


def rsi(c, n=14):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def r(x, nd=2):
    try:
        x = float(x)
        return None if math.isnan(x) or math.isinf(x) else round(x, nd)
    except (TypeError, ValueError):
        return None


def stats(values):
    v = np.array(values, dtype=float)
    if len(v) < MIN_N:
        return {"n": int(len(v))}
    return {"n": int(len(v)), "up": r((v > 0).mean() * 100, 0),
            "p10": r(np.percentile(v, 10) * 100, 1), "p25": r(np.percentile(v, 25) * 100, 1),
            "p50": r(np.percentile(v, 50) * 100, 1), "p75": r(np.percentile(v, 75) * 100, 1),
            "p90": r(np.percentile(v, 90) * 100, 1)}


def thin(positions):
    out, last = [], -10 ** 9
    for p in positions:
        if p - last >= SPACING:
            out.append(p)
            last = p
    return out


def analyse(c, hi, lo, bench, horizons, five_years):
    c = c.dropna()
    if len(c) < 260:
        return None
    ma50, ma200 = c.rolling(50).mean(), c.rolling(200).mean()
    rs = rsi(c)
    ema12, ema26 = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    hist = macd - macd.ewm(span=9, adjust=False).mean()
    p = float(c.iloc[-1])

    # ---- snapshot ----
    cross, cross_date = None, None
    diff = (ma50 - ma200).dropna()
    sign = np.sign(diff)
    changes = sign[sign != sign.shift(1)].iloc[1:]
    changes = changes[changes.index >= c.index[-1] - pd.Timedelta(days=365)]
    if len(changes):
        cross = "golden" if changes.iloc[-1] > 0 else "death"
        cross_date = changes.index[-1].date().isoformat()
    last252 = c.tail(252)
    h52, l52 = float(last252.max()), float(last252.min())
    hi60 = float((hi if hi is not None else c).reindex(c.index).tail(60).max())
    lo60 = float((lo if lo is not None else c).reindex(c.index).tail(60).min())
    rel = None
    if bench is not None:
        b = bench.reindex(c.index).ffill()
        if b.notna().sum() > 70:
            rel = r(((c.iloc[-1] / c.iloc[-64]) / (b.iloc[-1] / b.iloc[-64]) - 1) * 100, 1)
    snap = {
        "price": r(p, 4), "ma50": r(ma50.iloc[-1], 4), "ma200": r(ma200.iloc[-1], 4),
        "vs50": r((p / ma50.iloc[-1] - 1) * 100, 1), "vs200": r((p / ma200.iloc[-1] - 1) * 100, 1),
        "cross": cross, "cross_date": cross_date, "rsi": r(rs.iloc[-1], 0),
        "macd_up": bool(hist.iloc[-1] > 0), "macd_turn": bool(np.sign(hist.iloc[-1]) != np.sign(hist.iloc[-6])),
        "hi52": r(h52, 4), "lo52": r(l52, 4), "pos52": r((p - l52) / (h52 - l52) * 100 if h52 > l52 else 50, 0),
        "rel3m": rel, "support": r(lo60, 4), "resistance": r(hi60, 4),
    }
    checks = [p > ma50.iloc[-1], p > ma200.iloc[-1], ma50.iloc[-1] > ma200.iloc[-1], rs.iloc[-1] > 50,
              hist.iloc[-1] > 0, snap["pos52"] is not None and snap["pos52"] > 50]
    if rel is not None:
        checks.append(rel > 0)
    snap["positive"], snap["signals"] = int(sum(bool(x) for x in checks)), len(checks)

    # ---- what happened next ----
    zone = pd.cut(rs, [-1, 30, 50, 70, 101], labels=[0, 1, 2, 3]).astype(float)
    state = (c > ma200).astype(float) * 10 + zone
    state[ma200.isna() | rs.isna()] = np.nan
    today = state.iloc[-1]
    names = {0: "RSI below 30 (oversold)", 1: "RSI 30-50", 2: "RSI 50-70", 3: "RSI above 70 (stretched)"}
    setup = ("above" if today >= 10 else "below") + " its 200-day average, " + names.get(int(today % 10), "")
    arr, st = c.values, state.values
    outcomes = {}
    for key, h in horizons.items():
        fwd = np.full(len(arr), np.nan)
        fwd[:-h] = arr[h:] / arr[:-h] - 1
        valid = np.where(~np.isnan(fwd) & ~np.isnan(st))[0]
        same = thin([i for i in valid if st[i] == today])
        allp = thin(list(valid))
        outcomes[key] = {"similar": stats(fwd[same]), "all": stats(fwd[allp])}
    h5 = five_years
    if len(arr) > h5 + 50:
        f5 = arr[h5:] / arr[:-h5] - 1
        outcomes["5y"] = {"all": stats(f5[thin(list(range(len(f5))))]), "years": r(len(arr) / (h5 / 5), 1)}
    else:
        outcomes["5y"] = {"all": {"n": 0}, "years": r(len(arr) / (h5 / 5), 1)}
    return {"snap": snap, "setup": setup, "outcomes": outcomes,
            "history_from": c.index[0].date().isoformat()}


def download(tickers):
    frames = []
    for i in range(0, len(tickers), 150):
        frames.append(yf.download(tickers[i:i + 150], period="10y", interval="1d", group_by="ticker",
                                  auto_adjust=True, threads=True, progress=False))
    return pd.concat(frames, axis=1) if frames else pd.DataFrame()


def col(df, t, f):
    try:
        s = df[t][f]
        return s if s.notna().sum() else None
    except Exception:
        return None


def run(market, tickers_syms, bench_t, horizons, five, ref_price=None):
    tickers = [t for t, _ in tickers_syms] + ([bench_t] if bench_t else [])
    df = download(tickers)
    bench = col(df, bench_t, "Close") if bench_t else None
    out, skipped = {}, 0
    for t, sym in tickers_syms:
        c = col(df, t, "Close")
        if c is None:
            skipped += 1
            continue
        k = 1.0
        ref = (ref_price or {}).get(sym)
        if ref and c.dropna().size:
            ratio = ref / float(c.dropna().iloc[-1])
            if 50 < ratio < 200:          # quoted in pounds on Yahoo, pence on BullEng
                k = 100.0
        hi, lo = col(df, t, "High"), col(df, t, "Low")
        res = analyse(c * k, hi * k if hi is not None else None, lo * k if lo is not None else None,
                      bench, horizons, five)
        if res:
            out[sym] = res
        else:
            skipped += 1
    pathlib.Path(f"data/{market}/tech.json").write_text(json.dumps(
        {"updated": dt.date.today().isoformat(), "stocks": out}, separators=(",", ":")))
    print(f"{market.upper()}: technicals for {len(out)} (skipped {skipped} with too little history)")
    if out:
        k0 = next(iter(out))
        o = out[k0]["outcomes"]["1m"]
        print(f"  sample {k0}: {out[k0]['setup']}; 1 month similar n={o['similar'].get('n')} "
              f"up={o['similar'].get('up')}% median={o['similar'].get('p50')}%")


def main():
    stock_h = {"5d": 5, "1m": 21, "3m": 63, "1y": 252}
    for m, bench in (("us", "^GSPC"), ("uk", "^FTSE")):
        try:
            site = json.loads(pathlib.Path(f"data/{m}/site.json").read_text())
            pairs = [(s["full"], s["sym"]) for s in site["stocks"]]
            prices = {s["sym"]: s.get("price") for s in site["stocks"]}
            run(m, pairs, bench, stock_h, 1260, prices if m == "uk" else None)
        except Exception as e:
            print(f"{m.upper()} technicals failed: {e}")
    try:
        site = json.loads(pathlib.Path("data/crypto/site.json").read_text())
        coins = [c for c in site["coins"] if c["sym"] not in STABLE][:60]
        pairs = [(f"{c['sym']}-USD", c["id"]) for c in coins]
        run("crypto", pairs, "BTC-USD", {"5d": 5, "1m": 30, "3m": 91, "1y": 365}, 1826)
        # drop symbol mix-ups: Yahoo price must be close to CoinGecko's
        f = pathlib.Path("data/crypto/tech.json")
        d = json.loads(f.read_text())
        price = {c["id"]: c["price"] for c in coins}
        d["stocks"] = {k: v for k, v in d["stocks"].items()
                       if price.get(k) and 0.8 <= v["snap"]["price"] / price[k] <= 1.25}
        f.write_text(json.dumps(d, separators=(",", ":")))
        print(f"  CRYPTO: {len(d['stocks'])} coins kept after price check")
    except Exception as e:
        print(f"CRYPTO technicals failed: {e}")


if __name__ == "__main__":
    main()
