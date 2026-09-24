"""
BullEng - UK company fundamentals for intrinsic value estimates.

For each UK stock on BullEng, reads the latest annual financial statements
(via the yfinance library), converts them to pence per share - many FTSE
companies report in US dollars - and saves:

  data/uk/fundamentals.json
"""

import datetime as dt
import json
import math
import pathlib

import yfinance as yf

SITE = pathlib.Path("data/uk/site.json")
OUT = pathlib.Path("data/uk/fundamentals.json")
FINANCIALS = {"HSBA", "BARC", "LLOY", "NWG", "STAN", "PRU", "LGEN", "AV", "III"}   # no DCF


def val(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


def row(df, names):
    """Values of the first matching row, newest first, as (date, value) pairs."""
    if df is None or getattr(df, "empty", True):
        return []
    for n in names:
        if n in df.index:
            s = df.loc[n]
            return [(c, val(v)) for c, v in s.items() if val(v) is not None]
    return []


def latest(df, names):
    r = row(df, names)
    return r[0][1] if r else None


def main():
    site = json.loads(SITE.read_text())
    fx = {i["sym"]: i["value"] for i in site["indices"]}
    gbpusd, gbpeur = fx.get("GBPUSD=X"), fx.get("GBPEUR=X")
    to_pence = {"GBP": 100.0, "GBp": 1.0, "GBX": 1.0}
    if gbpusd:
        to_pence["USD"] = 100.0 / gbpusd
    if gbpeur:
        to_pence["EUR"] = 100.0 / gbpeur

    result, problems = {}, []
    for s in site["stocks"]:
        sym, full, price = s["sym"], s["full"], s["price"]
        try:
            t = yf.Ticker(full)
            info = t.get_info() or {}
            inc, bs, cf = t.income_stmt, t.balance_sheet, t.cashflow
        except Exception as e:
            problems.append(f"{sym}: download failed ({e})")
            continue
        cur = info.get("financialCurrency") or "GBP"
        k = to_pence.get(cur)
        if not k:
            problems.append(f"{sym}: reports in {cur}, no exchange rate")
            continue

        ni = latest(inc, ["Net Income Common Stockholders", "Net Income"])
        shares = latest(bs, ["Ordinary Shares Number", "Share Issued"])
        eps = latest(inc, ["Diluted EPS", "Basic EPS"])
        if eps is None and ni and shares:
            eps = ni / shares
        if not shares and ni and eps:
            shares = ni / eps
        if eps is None or not shares:
            problems.append(f"{sym}: EPS or share count missing")
            continue
        eq = latest(bs, ["Stockholders Equity", "Common Stock Equity"])
        ocf = latest(cf, ["Operating Cash Flow"])
        capex = latest(cf, ["Capital Expenditure"])            # negative number
        fcf = latest(cf, ["Free Cash Flow"])
        if fcf is None and ocf is not None:
            fcf = ocf + (capex or 0)
        divs = latest(cf, ["Cash Dividends Paid", "Common Stock Dividend Paid"])
        rev = row(inc, ["Total Revenue", "Operating Revenue"])

        eps_p = eps * k
        if eps_p > 0 and not 1 <= price / eps_p <= 300:
            problems.append(f"{sym}: EPS {eps_p:.1f}p does not fit price {price}p ({cur}) - skipped")
            continue
        if ni and abs(eps * shares / ni - 1) > 0.25:
            problems.append(f"{sym}: EPS x shares does not match profit - skipped")
            continue
        g = None
        if len(rev) >= 3 and rev[-1][1] > 0 and rev[0][1] > 0:
            g = (rev[0][1] / rev[-1][1]) ** (1 / (len(rev) - 1)) - 1
        dates = row(inc, ["Diluted EPS", "Basic EPS", "Net Income"])
        fy_end = dates[0][0].date().isoformat() if dates else None

        result[sym] = {
            "fy_end": fy_end, "currency": cur,
            "eps": round(eps_p, 3),
            "bvps": round(eq / shares * k, 3) if eq else None,
            "dps": round(abs(divs) / shares * k, 3) if divs else 0,
            "fcfps": None if sym in FINANCIALS or fcf is None else round(fcf / shares * k, 3),
            "growth": None if g is None else round(max(0.0, min(0.15, g)), 4),
            "rev_cagr": None if g is None else round(g, 4),
            "bank": sym in FINANCIALS,
        }

    OUT.write_text(json.dumps({"updated": dt.date.today().isoformat(),
                               "source": "Annual financial statements via Yahoo Finance (yfinance)",
                               "stocks": result}, indent=0))
    print(f"UK fundamentals saved for {len(result)} of {len(site['stocks'])} stocks")
    for sym, f in list(result.items())[:6]:
        print(f"  {sym}: {f['currency']} -> EPS {f['eps']}p, NAV {f['bvps']}p, FCF {f['fcfps']}p, year {f['fy_end']}")
    for p in problems:
        print("  " + p)


if __name__ == "__main__":
    main()
