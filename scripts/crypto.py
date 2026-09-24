"""
BullEng - crypto market data (free sources).

  CoinGecko public API : top 100 coins, category moves, global totals, Bitcoin history
  alternative.me       : Crypto Fear & Greed Index (credit shown on the site)

Writes data/crypto/site.json. An optional free CoinGecko "demo" key can be
added as the GitHub secret COINGECKO_KEY if the public API starts rate-limiting.
"""

import datetime as dt
import json
import os
import pathlib
import time

import requests

CG = "https://api.coingecko.com/api/v3/"
HEAD = {"User-Agent": "BullEng data job (github.com/bullengtrade/bulleng)", "Accept": "application/json"}
if os.environ.get("COINGECKO_KEY"):
    HEAD["x-cg-demo-api-key"] = os.environ["COINGECKO_KEY"]

CATEGORIES = {   # CoinGecko category id -> label shown on BullEng
    "layer-1": "Layer 1", "layer-2": "Layer 2", "decentralized-finance-defi": "DeFi",
    "stablecoins": "Stablecoins", "meme-token": "Meme coins", "artificial-intelligence": "AI tokens",
    "exchange-based-tokens": "Exchange tokens", "real-world-assets-rwa": "Real-world assets",
    "gaming": "Gaming", "decentralized-exchange": "DEX tokens",
}


def get(url, params=None, tries=4):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=HEAD, timeout=45)
            if r.status_code == 429:                  # rate limited - wait and retry
                last = "rate limited (429)"
                time.sleep(20 * (i + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            time.sleep(5 * (i + 1))
    raise RuntimeError(f"{url}: {last}")


def r2(x, nd=2):
    return None if x is None else round(float(x), nd)


def main():
    out = pathlib.Path("data/crypto")
    out.mkdir(parents=True, exist_ok=True)

    coins = get(CG + "coins/markets", {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 100,
                                       "page": 1, "price_change_percentage": "24h,7d,30d"})
    time.sleep(3)
    glob = get(CG + "global").get("data", {})
    time.sleep(3)
    cats = {c["id"]: c for c in get(CG + "coins/categories")}
    time.sleep(3)
    btc = get(CG + "coins/bitcoin/market_chart", {"vs_currency": "usd", "days": 90, "interval": "daily"})
    try:
        fng = requests.get("https://api.alternative.me/fng/", params={"limit": 30}, headers=HEAD, timeout=30).json()["data"]
    except Exception as e:
        print(f"Fear & Greed failed: {e}")
        fng = []

    rows = [{
        "id": c["id"], "sym": (c.get("symbol") or "").upper(), "name": c.get("name"), "rank": c.get("market_cap_rank"),
        "price": c.get("current_price"), "mcap": c.get("market_cap"), "volume": c.get("total_volume"),
        "pct": r2(c.get("price_change_percentage_24h_in_currency")), "pct_7d": r2(c.get("price_change_percentage_7d_in_currency")),
        "pct_30d": r2(c.get("price_change_percentage_30d_in_currency")),
        "high": c.get("high_24h"), "low": c.get("low_24h"),
        "circ": c.get("circulating_supply"), "max": c.get("max_supply"),
        "ath": c.get("ath"), "ath_pct": r2(c.get("ath_change_percentage")), "ath_date": (c.get("ath_date") or "")[:10],
    } for c in coins]
    movers = sorted([c for c in rows if c["pct"] is not None and (c["volume"] or 0) > 5e6], key=lambda c: c["pct"])
    categories = [{"code": k, "name": v, "mcap": cats[k].get("market_cap"), "pct_1d": r2(cats[k].get("market_cap_change_24h")),
                   "volume": cats[k].get("volume_24h")} for k, v in CATEGORIES.items() if k in cats]

    site = {
        "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
        "total_mcap": glob.get("total_market_cap", {}).get("usd"),
        "total_volume": glob.get("total_volume", {}).get("usd"),
        "mcap_pct": r2(glob.get("market_cap_change_percentage_24h_usd")),
        "btc_dom": r2(glob.get("market_cap_percentage", {}).get("btc")),
        "eth_dom": r2(glob.get("market_cap_percentage", {}).get("eth")),
        "coins": rows, "categories": categories,
        "gainers": [c["id"] for c in reversed(movers[-5:]) if c["pct"] > 0],
        "losers": [c["id"] for c in movers[:5] if c["pct"] < 0],
        "btc_history": [[dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).date().isoformat(), r2(p)]
                        for t, p in btc.get("prices", [])],
        "fear_greed": [{"value": int(f["value"]), "label": f["value_classification"],
                        "date": dt.datetime.fromtimestamp(int(f["timestamp"]), dt.timezone.utc).date().isoformat()}
                       for f in fng],
    }
    (out / "site.json").write_text(json.dumps(site, separators=(",", ":")))
    fg = site["fear_greed"][0] if site["fear_greed"] else None
    print(f"Crypto: {len(rows)} coins, {len(categories)} categories, {len(site['btc_history'])} days of BTC history, "
          f"Fear & Greed {fg['value']} ({fg['label']})" if fg else "Fear & Greed unavailable")


if __name__ == "__main__":
    main()
