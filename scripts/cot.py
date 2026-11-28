"""
BullEng - how big money is positioned in futures (CFTC Commitments of Traders).

Every Friday the US regulator (CFTC) publishes positions as of Tuesday for each
futures market, split by type of trader. From the "Traders in Financial
Futures" report BullEng keeps:

  leveraged funds  - hedge funds and other speculative money managers
  asset managers   - pension funds, insurers, mutual funds, ETFs

for CME Bitcoin, CME Ether and E-mini S&P 500 futures. Free, no key needed.
Writes data/cot.json.
"""

import datetime as dt
import json
import pathlib

import requests

API = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"
OUT = pathlib.Path("data/cot.json")
MARKETS = {   # key: (label, CFTC contract code, name to fall back on)
    "btc": ("CME Bitcoin futures", "133741", "BITCOIN - CHICAGO MERCANTILE EXCHANGE"),
    "eth": ("CME Ether futures", "146021", "ETHER CASH SETTLED - CHICAGO MERCANTILE EXCHANGE"),
    "spx": ("E-mini S&P 500 futures", "13874A", "E-MINI S&P 500 - CHICAGO MERCANTILE EXCHANGE"),
}
FIELDS = ["report_date_as_yyyy_mm_dd", "market_and_exchange_names", "cftc_contract_market_code", "open_interest_all",
          "lev_money_positions_long", "lev_money_positions_short",
          "asset_mgr_positions_long", "asset_mgr_positions_short"]
WEEKS = 156


def fetch(where):
    since = (dt.date.today() - dt.timedelta(weeks=WEEKS + 2)).isoformat()
    params = {"$select": ",".join(FIELDS), "$where": f"{where} AND report_date_as_yyyy_mm_dd >= '{since}'",
              "$order": "report_date_as_yyyy_mm_dd ASC", "$limit": 1000}
    r = requests.get(API, params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def main():
    out = {"updated": dt.date.today().isoformat(), "markets": {}}
    for key, (label, code, name) in MARKETS.items():
        try:
            rows = fetch(f"cftc_contract_market_code = '{code}'")
            if not rows:
                rows = fetch(f"market_and_exchange_names = '{name}'")
        except Exception as e:
            print(f"{label}: failed ({e})")
            continue
        n = lambda r, k: int(float(r.get(k) or 0))
        pts = [[r["report_date_as_yyyy_mm_dd"][:10], n(r, "lev_money_positions_long"), n(r, "lev_money_positions_short"),
                n(r, "asset_mgr_positions_long"), n(r, "asset_mgr_positions_short"), n(r, "open_interest_all")]
               for r in rows]
        if not pts:
            print(f"{label}: no rows found")
            continue
        out["markets"][key] = {"label": label, "name": rows[-1].get("market_and_exchange_names"),
                               "cols": ["date", "lev_long", "lev_short", "am_long", "am_short", "oi"], "rows": pts}
        d, ll, ls, al, as_, oi = pts[-1]
        print(f"{label} {d}: hedge funds net {ll - ls:+,}, asset managers net {al - as_:+,} "
              f"contracts ({len(pts)} weeks)")
    if out["markets"]:
        OUT.write_text(json.dumps(out, separators=(",", ":")))


if __name__ == "__main__":
    main()
