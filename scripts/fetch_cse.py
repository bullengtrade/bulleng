"""
BullEng - Colombo Stock Exchange daily data collector.

Runs on GitHub Actions every weekday after the CSE close.
Calls the public JSON endpoints that the cse.lk website itself uses
(unofficial, no API key needed) and saves:

  data/lk/latest.json          - the newest snapshot (the website will read this)
  data/lk/raw/YYYY-MM-DD.json  - a permanent daily archive
"""

import datetime as dt
import json
import pathlib
import time

import requests

BASE = "https://www.cse.lk/api/"

ENDPOINTS = {
    "marketStatus": "Market open or closed",
    "marketSummery": "Turnover, volume and number of trades",
    "dailyMarketSummery": "Daily summary incl. P/E, P/B, foreign flows",
    "aspiData": "All Share Price Index value and change",
    "snpData": "S&P Sri Lanka 20 index value and change",
    "allSectors": "Every sector index with its change",
    "tradeSummary": "Price, change and volume for every listed stock",
    "topGainers": "Top gaining stocks",
    "topLooses": "Top losing stocks",
    "mostActiveTrades": "Most actively traded stocks",
}

HEADERS = {
    "User-Agent": "BullEng data job (github.com/bullengtrade/bulleng)",
    "Accept": "application/json",
}

SL_TZ = dt.timezone(dt.timedelta(hours=5, minutes=30))


def post(name, tries=3):
    err = None
    for attempt in range(1, tries + 1):
        try:
            r = requests.post(BASE + name, data={}, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            err = e
            time.sleep(5 * attempt)
    print(f"  FAILED {name}: {err}")
    return None


def main():
    now = dt.datetime.now(SL_TZ)
    day = now.date().isoformat()
    print(f"Fetching CSE data for {day} (Sri Lanka time {now:%H:%M})")

    out = {"fetched_at": now.isoformat(), "source": "cse.lk public endpoints (unofficial)"}
    ok = 0
    for name, what in ENDPOINTS.items():
        result = post(name)
        out[name] = result
        if result is not None:
            ok += 1
            print(f"  ok  {name:20s} {what}")
        time.sleep(1)

    if ok == 0:
        raise SystemExit("All CSE requests failed - nothing saved.")

    root = pathlib.Path("data/lk")
    (root / "raw").mkdir(parents=True, exist_ok=True)
    (root / "raw" / f"{day}.json").write_text(json.dumps(out, separators=(",", ":")))
    (root / "latest.json").write_text(json.dumps(out, indent=1))
    print(f"Saved {ok}/{len(ENDPOINTS)} datasets for {day}")


if __name__ == "__main__":
    main()
