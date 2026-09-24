"""
BullEng - turn raw CSE data into the file the website reads.

Reads   data/lk/latest.json and the daily archive in data/lk/raw/
Writes  data/lk/site.json
"""

import datetime as dt
import json
import pathlib

ROOT = pathlib.Path("data/lk")
SL = dt.timezone(dt.timedelta(hours=5, minutes=30))
MIN_TURNOVER = 1_000_000  # Rs 1mn: ignore tiny trades when picking gainers/losers


def day_of(ms):
    return dt.datetime.fromtimestamp(ms / 1000, SL).date().isoformat()


def num(x, nd=2):
    return None if x is None else round(float(x), nd)


def index_block(d):
    return {"value": num(d.get("value")), "change": num(d.get("change")),
            "pct": num(d.get("percentage"), 3), "high": num(d.get("highValue")),
            "low": num(d.get("lowValue"))}


def history():
    """One entry per trading day from the raw archive (holidays are skipped)."""
    days = {}
    for f in sorted((ROOT / "raw").glob("*.json")):
        try:
            r = json.loads(f.read_text())
            a = r["aspiData"]
            td = day_of(a["timestamp"])
            days[td] = {"aspi": a["value"],
                        "sectors": {s["symbol"]: s["indexValue"] for s in (r.get("allSectors") or [])}}
        except Exception:
            continue
    return days


def pct_since(hist_days, symbol, today_value, sessions):
    """% change versus `sessions` trading days ago, if we have that much history."""
    if len(hist_days) <= sessions:
        return None
    past = hist_days[-1 - sessions][1]["sectors"].get(symbol)
    return None if not past else num((today_value / past - 1) * 100, 2)


def main():
    raw = json.loads((ROOT / "latest.json").read_text())
    trade_date = day_of(raw["aspiData"]["timestamp"])

    dms = (raw.get("dailyMarketSummery") or [[{}]])[0][0]
    buy, sell = dms.get("equityForeignPurchase") or 0, dms.get("equityForeignSales") or 0
    market = {
        "turnover": dms.get("equityTurnover"), "trades": dms.get("tradesNo"),
        "foreign_buy": buy, "foreign_sell": sell, "foreign_net": buy - sell,
        "market_cap": dms.get("marketCap"), "per": dms.get("per"), "pbv": dms.get("pbv"),
        "dy": dms.get("dy"), "listed": dms.get("listedCompanyNumber"),
        "traded": dms.get("tradeCompanyNumber"),
    }

    hist = sorted(history().items())

    sectors = []
    for s in raw.get("allSectors") or []:
        if s.get("sectorTurnoverToday") is None:  # skips the ASPI and S&P SL20 rows
            continue
        v = s["indexValue"]
        sectors.append({
            "code": s["symbol"], "name": s["name"], "value": num(v),
            "pct_1d": num(s.get("percentage")), "pct_1w": pct_since(hist, s["symbol"], v, 5),
            "pct_1m": pct_since(hist, s["symbol"], v, 21), "turnover": s.get("sectorTurnoverToday"),
        })

    stocks = []
    for t in (raw.get("tradeSummary") or {}).get("reqTradeSummery") or []:
        sym = t.get("symbol") or ""
        base, _, cls = sym.partition(".")
        stocks.append({
            "sym": base, "full": sym, "cls": cls[:1],  # N = voting, X = non-voting, U = fund units
            "name": (t.get("name") or "").title(), "price": t.get("price"),
            "change": t.get("change"), "pct": num(t.get("percentageChange")),
            "prev": t.get("previousClose"), "high": t.get("high"), "low": t.get("low"),
            "turnover": t.get("turnover"), "volume": t.get("sharevolume"),
            "trades": t.get("tradevolume"), "mcap": t.get("marketCap"),
        })

    shares = [s for s in stocks if s["cls"] in ("N", "X") and s["pct"] is not None]
    traded = [s for s in shares if (s["trades"] or 0) > 0]
    liquid = [s for s in traded if (s["turnover"] or 0) >= MIN_TURNOVER]
    by_pct = sorted(liquid, key=lambda s: s["pct"])

    site = {
        "updated": raw.get("fetched_at"), "trade_date": trade_date,
        "indices": {"aspi": index_block(raw["aspiData"]), "sl20": index_block(raw["snpData"])},
        "market": market,
        "breadth": {"up": sum(s["pct"] > 0 for s in traded),
                    "down": sum(s["pct"] < 0 for s in traded),
                    "flat": sum(s["pct"] == 0 for s in traded)},
        "sectors": sectors,
        "gainers": [s["full"] for s in reversed(by_pct[-5:]) if s["pct"] > 0],
        "losers": [s["full"] for s in by_pct[:5] if s["pct"] < 0],
        "aspi_history": [[d, v["aspi"]] for d, v in hist][-260:],
        "stocks": stocks,
    }
    (ROOT / "site.json").write_text(json.dumps(site, separators=(",", ":")))
    print(f"site.json for {trade_date}: {len(sectors)} sectors, {len(stocks)} securities, "
          f"{len(hist)} days of history")


if __name__ == "__main__":
    main()
