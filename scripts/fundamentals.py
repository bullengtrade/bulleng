"""
BullEng - US company fundamentals from official SEC filings (free).

For each US stock on BullEng, reads the company's XBRL financial data from
SEC EDGAR and saves per-share figures from its latest annual report (10-K):

  data/us/fundamentals.json

The website uses these to calculate intrinsic value estimates.
Runs weekly - annual figures only change when a new 10-K is filed.
"""

import datetime as dt
import json
import pathlib
import time

import requests

HEADERS = {"User-Agent": "BullEng research bullengtrade@gmail.com"}   # the SEC asks for a contact
SITE = pathlib.Path("data/us/site.json")
OUT = pathlib.Path("data/us/fundamentals.json")
BANKS = {"JPM", "BAC", "WFC", "GS", "MS"}      # no DCF for banks

CONCEPTS = {
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted", "EarningsPerShareBasic"],
    "net_income": ["NetIncomeLoss"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "shares": ["WeightedAverageNumberOfDilutedSharesOutstanding", "WeightedAverageNumberOfSharesOutstandingBasic"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "dps": ["CommonStockDividendsPerShareDeclared", "CommonStockDividendsPerShareCashPaid"],
    "dividends": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"],
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                "RevenuesNetOfInterestExpense"],
}


def annual(facts, names, unit_hint):
    """{fiscal year end date: value} from 10-K filings, latest filing wins."""
    gaap = facts.get("facts", {}).get("us-gaap", {})
    for name in names:
        units = gaap.get(name, {}).get("units", {})
        vals = next((v for k, v in units.items() if k == unit_hint), None)
        if not vals:
            continue
        out = {}
        for f in vals:
            if f.get("form") not in ("10-K", "10-K/A") or f.get("fp") != "FY":
                continue
            if "start" in f:   # flow item: must cover about a year
                days = (dt.date.fromisoformat(f["end"]) - dt.date.fromisoformat(f["start"])).days
                if not 340 <= days <= 380:
                    continue
            prev = out.get(f["end"])
            if prev is None or f.get("filed", "") >= prev[1]:
                out[f["end"]] = (f["val"], f.get("filed", ""))
        if out:
            return {k: v[0] for k, v in sorted(out.items())}
    return {}


def cagr(series, years=5):
    pts = sorted(series.items())
    if len(pts) < 3:
        return None
    pts = pts[-(years + 1):]
    first, last = pts[0][1], pts[-1][1]
    n = len(pts) - 1
    if first <= 0 or last <= 0:
        return None
    return (last / first) ** (1 / n) - 1


def main():
    site = json.loads(SITE.read_text())
    tick = requests.get("https://www.sec.gov/files/company_tickers.json", headers=HEADERS, timeout=30).json()
    cik = {v["ticker"].upper(): v["cik_str"] for v in tick.values()}
    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    result, problems = {}, []

    for s in site["stocks"]:
        sym, price = s["sym"], s["price"]
        c = cik.get(sym.upper()) or cik.get(sym.upper().replace("-", "."))
        if not c:
            problems.append(f"{sym}: no SEC id")
            continue
        try:
            r = requests.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(c):010d}.json",
                             headers=HEADERS, timeout=60)
            r.raise_for_status()
            facts = r.json()
        except Exception as e:
            problems.append(f"{sym}: {e}")
            if sym in old:
                result[sym] = old[sym]           # keep last good figures
            continue
        time.sleep(0.25)                          # stay well under the SEC's 10 requests/second

        eps, ni = annual(facts, CONCEPTS["eps"], "USD/shares"), annual(facts, CONCEPTS["net_income"], "USD")
        eq, sh = annual(facts, CONCEPTS["equity"], "USD"), annual(facts, CONCEPTS["shares"], "shares")
        ocf, capex = annual(facts, CONCEPTS["ocf"], "USD"), annual(facts, CONCEPTS["capex"], "USD")
        dps, divs = annual(facts, CONCEPTS["dps"], "USD/shares"), annual(facts, CONCEPTS["dividends"], "USD")
        rev = annual(facts, CONCEPTS["revenue"], "USD")
        if not sh:   # fall back to the cover-page share count
            dei = facts.get("facts", {}).get("dei", {}).get("EntityCommonStockSharesOutstanding", {})
            vals = sorted(dei.get("units", {}).get("shares", []), key=lambda f: f.get("end", ""))
            if vals:
                sh = {vals[-1]["end"]: vals[-1]["val"]}
        if not eps and ni and sh:
            eps = {k: v / sh[max(sh)] for k, v in ni.items()}
        if not eps or not sh:
            problems.append(f"{sym}: EPS or share count missing")
            continue

        fy = max(eps)                             # latest fiscal year end
        shares = sh.get(fy) or sh[max(sh)]
        e = eps[fy]
        # sanity check: P/E between 1 and 1000, otherwise the per-share basis is wrong (share classes, splits)
        if e > 0 and not 1 <= price / e <= 1000:
            problems.append(f"{sym}: EPS {e} does not fit price {price} - skipped")
            continue
        eq_fy = eq.get(fy) or (eq[max(eq)] if eq else None)
        d = dps.get(fy)
        if d is None and divs.get(fy):
            d = divs[fy] / shares
        fcf = (ocf[fy] - capex.get(fy, 0)) / shares if fy in ocf else None
        g = cagr(rev)

        result[sym] = {
            "fy_end": fy, "eps": round(e, 4),
            "bvps": round(eq_fy / shares, 4) if eq_fy else None,
            "dps": round(d, 4) if d else 0,
            "fcfps": None if sym in BANKS or fcf is None else round(fcf, 4),
            "growth": None if g is None else round(max(0.0, min(0.15, g)), 4),
            "rev_cagr": None if g is None else round(g, 4),
            "net_income": ni.get(fy), "bank": sym in BANKS,
        }

    OUT.write_text(json.dumps({"updated": dt.date.today().isoformat(), "source": "SEC EDGAR 10-K filings",
                               "stocks": result}, indent=0))
    print(f"Fundamentals saved for {len(result)} of {len(site['stocks'])} US stocks")
    for p in problems:
        print("  " + p)


if __name__ == "__main__":
    main()
