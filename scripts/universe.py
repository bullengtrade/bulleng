"""
BullEng - index member lists (S&P 500 and FTSE 100), refreshed automatically.

Reads the constituent tables maintained on Wikipedia and writes
data/universe.json:  {"us": [{"t": "AAPL", "name": "...", "sector": "Technology"}, ...],
                      "uk": [{"t": "SHEL.L", ...}]}
If a download fails, the previous list is kept.
"""

import datetime as dt
import io
import json
import pathlib
import re

import pandas as pd
import requests

OUT = pathlib.Path("data/universe.json")
UA = {"User-Agent": "BullEng research bot (github.com/bullengtrade/bulleng; bullengtrade@gmail.com)"}

US_SECTOR = {"Information Technology": "Technology"}   # GICS name -> BullEng name (others match)

# FTSE/ICB sector wording -> BullEng's 11 UK industries (checked in order)
UK_RULES = [
    ("Real Estate", ["real estate", "reit", "property"]),
    ("Utilities", ["utilit", "electricity", "water", "gas, water", "multi-utilit"]),
    ("Telecommunications", ["telecom"]),
    ("Health Care", ["pharma", "health", "medical", "biotech"]),
    ("Technology", ["software", "technology", "computer"]),
    ("Energy", ["oil", "gas", "coal", "energy", "renewable"]),
    ("Financials", ["bank", "insurance", "life", "financ", "investment", "asset", "equity investment", "capital"]),
    ("Consumer Staples", ["food retail", "tobacco", "beverage", "food producer", "personal care", "drug", "grocer"]),
    ("Basic Materials", ["mining", "metal", "chemical", "industrial materials", "precious", "forestry", "paper"]),
    ("Consumer Discretionary", ["retail", "travel", "leisure", "media", "hotel", "restaurant", "household goods",
                                "home construction", "housebuild", "personal goods", "automobile", "gambling",
                                "consumer services"]),
    ("Industrials", ["aerospace", "defen", "industrial", "construction", "engineering", "support", "transport",
                     "electronic", "electrical", "business services", "packaging"]),
]


def tables(url):
    r = requests.get(url, headers=UA, timeout=45)
    r.raise_for_status()
    return pd.read_html(io.StringIO(r.text))


def col(df, *words):
    for c in df.columns:
        name = " ".join(map(str, c)) if isinstance(c, tuple) else str(c)
        if any(w in name.lower() for w in words):
            return c
    return None


def us_list():
    for df in tables("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"):
        sym, name, sec = col(df, "symbol", "ticker"), col(df, "security", "company"), col(df, "gics sector")
        if sym is None or name is None or sec is None or len(df) < 400:
            continue
        out = []
        for _, r in df.iterrows():
            t = str(r[sym]).strip().replace(".", "-")          # BRK.B -> BRK-B (Yahoo format)
            s = str(r[sec]).strip()
            out.append({"t": t, "name": str(r[name]).strip(), "sector": US_SECTOR.get(s, s)})
        return out
    raise RuntimeError("S&P 500 table not found")


def uk_sector(text):
    t = str(text).lower()
    for industry, words in UK_RULES:
        if any(w in t for w in words):
            return industry
    return None


def uk_list():
    for df in tables("https://en.wikipedia.org/wiki/FTSE_100_Index"):
        sym, name, sec = col(df, "ticker", "epic", "symbol"), col(df, "company"), col(df, "sector", "industry")
        if sym is None or name is None or len(df) < 90:
            continue
        out, unknown = [], set()
        for _, r in df.iterrows():
            raw = str(r[sym]).strip().rstrip(".")
            if not raw or raw == "nan":
                continue
            t = re.sub(r"\.", "-", raw) + ".L"                   # BT.A -> BT-A.L, BP. -> BP.L
            s = uk_sector(r[sec]) if sec is not None else None
            if not s:
                unknown.add(str(r[sec]) if sec is not None else "?")
                s = "Industrials"
            out.append({"t": t, "name": str(r[name]).strip(), "sector": s})
        if unknown:
            print(f"  UK sectors not matched (shown as Industrials): {sorted(unknown)[:10]}")
        return out
    raise RuntimeError("FTSE 100 table not found")


def main():
    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    out = {"updated": dt.date.today().isoformat()}
    for key, fn in (("us", us_list), ("uk", uk_list)):
        try:
            lst = fn()
            out[key] = lst
            secs = {}
            for x in lst:
                secs[x["sector"]] = secs.get(x["sector"], 0) + 1
            print(f"{key.upper()}: {len(lst)} companies; sectors: {dict(sorted(secs.items(), key=lambda s: -s[1]))}")
        except Exception as e:
            print(f"{key.upper()} list failed ({e}); keeping previous list")
            if key in old:
                out[key] = old[key]
    OUT.write_text(json.dumps(out, indent=0))


if __name__ == "__main__":
    main()
