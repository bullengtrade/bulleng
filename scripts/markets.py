"""
BullEng - US and UK daily market data.

Downloads end-of-day prices (free, via the yfinance library) for the main
indices, the 11 US sector funds and a list of large US and UK companies, then writes:

  data/us/site.json   data/uk/site.json

Unlike the CSE, three months of history comes with every download, so the
1-week and 1-month views and the index chart work from day one.
"""

import datetime as dt
import json
import math
import pathlib

import yfinance as yf

US = {
    "indices": [("^GSPC", "S&P 500"), ("^IXIC", "Nasdaq Composite"), ("^DJI", "Dow Jones"), ("^VIX", "VIX volatility")],
    "sector_funds": {
        "XLK": "Technology", "XLC": "Communication Services", "XLY": "Consumer Discretionary",
        "XLP": "Consumer Staples", "XLF": "Financials", "XLV": "Health Care", "XLI": "Industrials",
        "XLE": "Energy", "XLU": "Utilities", "XLRE": "Real Estate", "XLB": "Materials"},
    "stocks": {
        "Technology": [("AAPL", "Apple"), ("MSFT", "Microsoft"), ("NVDA", "Nvidia"), ("AVGO", "Broadcom"),
                       ("ORCL", "Oracle"), ("CRM", "Salesforce"), ("AMD", "AMD"), ("ADBE", "Adobe"),
                       ("CSCO", "Cisco"), ("INTC", "Intel"), ("QCOM", "Qualcomm"), ("IBM", "IBM"),
                       ("TXN", "Texas Instruments"), ("MU", "Micron")],
        "Communication Services": [("GOOGL", "Alphabet"), ("META", "Meta Platforms"), ("NFLX", "Netflix"),
                                   ("DIS", "Walt Disney"), ("T", "AT&T"), ("VZ", "Verizon"), ("TMUS", "T-Mobile US")],
        "Consumer Discretionary": [("AMZN", "Amazon"), ("TSLA", "Tesla"), ("HD", "Home Depot"),
                                   ("MCD", "McDonald's"), ("NKE", "Nike"), ("SBUX", "Starbucks"),
                                   ("LOW", "Lowe's"), ("BKNG", "Booking Holdings")],
        "Consumer Staples": [("WMT", "Walmart"), ("PG", "Procter & Gamble"), ("KO", "Coca-Cola"),
                             ("PEP", "PepsiCo"), ("COST", "Costco"), ("PM", "Philip Morris")],
        "Financials": [("BRK-B", "Berkshire Hathaway"), ("JPM", "JPMorgan Chase"), ("V", "Visa"),
                       ("MA", "Mastercard"), ("BAC", "Bank of America"), ("WFC", "Wells Fargo"),
                       ("GS", "Goldman Sachs"), ("MS", "Morgan Stanley")],
        "Health Care": [("LLY", "Eli Lilly"), ("UNH", "UnitedHealth"), ("JNJ", "Johnson & Johnson"),
                        ("ABBV", "AbbVie"), ("MRK", "Merck"), ("PFE", "Pfizer"), ("TMO", "Thermo Fisher")],
        "Industrials": [("CAT", "Caterpillar"), ("GE", "GE Aerospace"), ("BA", "Boeing"), ("HON", "Honeywell"),
                        ("UPS", "UPS"), ("RTX", "RTX"), ("DE", "Deere")],
        "Energy": [("XOM", "Exxon Mobil"), ("CVX", "Chevron"), ("COP", "ConocoPhillips")],
        "Utilities": [("NEE", "NextEra Energy"), ("DUK", "Duke Energy"), ("SO", "Southern Company")],
        "Real Estate": [("PLD", "Prologis"), ("AMT", "American Tower")],
        "Materials": [("LIN", "Linde"), ("SHW", "Sherwin-Williams"), ("FCX", "Freeport-McMoRan")],
    },
    "pence": False,
}

UK = {
    "indices": [("^FTSE", "FTSE 100"), ("^FTMC", "FTSE 250"), ("GBPUSD=X", "GBP/USD"), ("GBPEUR=X", "GBP/EUR")],
    "sector_funds": {},
    "stocks": {
        "Energy": [("SHEL.L", "Shell"), ("BP.L", "BP")],
        "Financials": [("HSBA.L", "HSBC"), ("BARC.L", "Barclays"), ("LLOY.L", "Lloyds Banking Group"),
                       ("NWG.L", "NatWest Group"), ("STAN.L", "Standard Chartered"),
                       ("LSEG.L", "London Stock Exchange Group"), ("PRU.L", "Prudential"),
                       ("LGEN.L", "Legal & General"), ("AV.L", "Aviva"), ("III.L", "3i Group")],
        "Health Care": [("AZN.L", "AstraZeneca"), ("GSK.L", "GSK"), ("SN.L", "Smith & Nephew")],
        "Consumer Staples": [("ULVR.L", "Unilever"), ("BATS.L", "British American Tobacco"), ("DGE.L", "Diageo"),
                             ("TSCO.L", "Tesco"), ("SBRY.L", "Sainsbury's"), ("IMB.L", "Imperial Brands"),
                             ("RKT.L", "Reckitt")],
        "Industrials": [("BA.L", "BAE Systems"), ("RR.L", "Rolls-Royce"), ("EXPN.L", "Experian"),
                        ("ITRK.L", "Intertek"), ("BNZL.L", "Bunzl")],
        "Basic Materials": [("RIO.L", "Rio Tinto"), ("GLEN.L", "Glencore"), ("AAL.L", "Anglo American"),
                            ("ANTO.L", "Antofagasta")],
        "Consumer Discretionary": [("CPG.L", "Compass Group"), ("NXT.L", "Next"), ("REL.L", "RELX"),
                                   ("IAG.L", "International Airlines Group"), ("WTB.L", "Whitbread")],
        "Utilities": [("NG.L", "National Grid"), ("SSE.L", "SSE"), ("UU.L", "United Utilities"),
                      ("SVT.L", "Severn Trent")],
        "Technology": [("SGE.L", "Sage Group")],
        "Telecommunications": [("VOD.L", "Vodafone"), ("BT-A.L", "BT Group")],
        "Real Estate": [("SGRO.L", "Segro"), ("LAND.L", "Land Securities"), ("BLND.L", "British Land")],
    },
    "pence": True,   # London prices are quoted in pence
}


def num(x, nd=2):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else round(x, nd)


def series(df, ticker, field):
    try:
        s = df[ticker][field] if isinstance(df.columns[0], tuple) or ticker in df.columns.get_level_values(0) else df[field]
    except Exception:
        return None
    s = s.dropna()
    return s if len(s) else None


def pct_back(close, n):
    if close is None or len(close) <= n:
        return None
    return num((close.iloc[-1] / close.iloc[-1 - n] - 1) * 100)


def load_universe(key, cfg):
    """Use the full index member list (data/universe.json) when available."""
    try:
        u = json.loads(pathlib.Path("data/universe.json").read_text()).get(key)
        if u and len(u) >= 50:
            grouped = {}
            for x in u:
                grouped.setdefault(x["sector"], []).append((x["t"], x["name"]))
            cfg["stocks"] = grouped
            print(f"  {key.upper()}: using index member list ({len(u)} companies)")
    except Exception as e:
        print(f"  {key.upper()}: member list unavailable ({e}), using built-in list")


def build(key, cfg):
    load_universe(key, cfg)
    stock_rows = [(t, n, sec) for sec, lst in cfg["stocks"].items() for t, n in lst]
    tickers = [t for t, _ in cfg["indices"]] + list(cfg["sector_funds"]) + [t for t, _, _ in stock_rows]
    df = yf.download(tickers, period="3mo", interval="1d", group_by="ticker",
                     auto_adjust=False, threads=True, progress=False)
    missing = []
    div = 100 if cfg["pence"] else 1   # turn pence x volume into pounds
    scale = {}                         # a few London stocks are quoted in pounds, not pence
    if cfg["pence"]:
        for t, _, _ in stock_rows:
            try:
                if yf.Ticker(t).fast_info["currency"] == "GBP":
                    scale[t] = 100.0
            except Exception:
                pass
        if scale:
            print(f"  quoted in pounds, converted to pence: {', '.join(scale)}")

    def quote(t):
        c, v = series(df, t, "Close"), series(df, t, "Volume")
        h, lo = series(df, t, "High"), series(df, t, "Low")
        if c is None or len(c) < 2:
            missing.append(t)
            return None
        k = scale.get(t, 1.0)
        c = c * k
        h = h * k if h is not None else None
        lo = lo * k if lo is not None else None
        last, prev = float(c.iloc[-1]), float(c.iloc[-2])
        vol = float(v.iloc[-1]) if v is not None else 0.0
        return {"price": num(last), "prev": num(prev), "change": num(last - prev),
                "pct": num((last / prev - 1) * 100), "pct_1w": pct_back(c, 5), "pct_1m": pct_back(c, 21),
                "high": num(h.iloc[-1]) if h is not None else None, "low": num(lo.iloc[-1]) if lo is not None else None,
                "volume": int(vol), "turnover": num(last * vol / div, 0), "date": c.index[-1].date().isoformat()}

    indices = []
    for t, name in cfg["indices"]:
        q = quote(t)
        if q:
            indices.append({"sym": t, "name": name, "value": q["price"], "change": q["change"], "pct": q["pct"]})

    stocks = []
    for t, name, sec in stock_rows:
        q = quote(t)
        if q:
            stocks.append({"sym": t.replace(".L", ""), "full": t, "name": name, "sector": sec, **q})

    sectors = []
    if cfg["sector_funds"]:                       # US: use the sector funds
        for t, name in cfg["sector_funds"].items():
            q = quote(t)
            if q:
                sectors.append({"code": t, "name": name, "pct_1d": q["pct"], "pct_1w": q["pct_1w"],
                                "pct_1m": q["pct_1m"], "turnover": q["turnover"]})
    else:                                         # UK: average of the companies in each sector
        for sec in cfg["stocks"]:
            m = [s for s in stocks if s["sector"] == sec]
            if not m:
                continue
            avg = lambda k: num(sum(s[k] for s in m if s[k] is not None) / max(1, sum(s[k] is not None for s in m)))
            code = "".join(w[0] for w in sec.split()).upper() if " " in sec else sec[:4].upper()
            sectors.append({"code": code, "name": sec, "pct_1d": avg("pct"),
                            "pct_1w": avg("pct_1w"), "pct_1m": avg("pct_1m"),
                            "turnover": sum(s["turnover"] or 0 for s in m)})

    by_pct = sorted([s for s in stocks if s["pct"] is not None], key=lambda s: s["pct"])
    main = series(df, cfg["indices"][0][0], "Close")
    site = {
        "market": key, "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
        "trade_date": main.index[-1].date().isoformat() if main is not None else None,
        "indices": indices, "sectors": sectors, "stocks": stocks,
        "gainers": [s["full"] for s in reversed(by_pct[-5:]) if s["pct"] > 0],
        "losers": [s["full"] for s in by_pct[:5] if s["pct"] < 0],
        "breadth": {"up": sum(s["pct"] > 0 for s in by_pct), "down": sum(s["pct"] < 0 for s in by_pct),
                    "flat": sum(s["pct"] == 0 for s in by_pct)},
        "index_history": [[d.date().isoformat(), num(v)] for d, v in main.items()] if main is not None else [],
        "pence": cfg["pence"],
    }
    out = pathlib.Path(f"data/{key}")
    out.mkdir(parents=True, exist_ok=True)
    (out / "site.json").write_text(json.dumps(site, separators=(",", ":")))
    print(f"{key.upper()}: {len(indices)} indices, {len(sectors)} sectors, {len(stocks)} stocks, "
          f"trade date {site['trade_date']}, {len(site['index_history'])} days of index history")
    if missing:
        print(f"  no data for: {', '.join(missing)}")
    return len(stocks)


def main():
    ok = build("us", US) + build("uk", UK)
    if ok == 0:
        raise SystemExit("No US or UK data downloaded.")


if __name__ == "__main__":
    main()
