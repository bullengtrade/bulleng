"""
BullEng - UK short positions (FCA aggregated net short positions), free.

Since 13 July 2026 the FCA publishes, every working day from 12:00, the total
net short position held against each UK-traded company by all reporting funds
(each fund's position of 0.2% of the shares or more is added up). Individual
fund names are no longer published.

Matches the companies to the FTSE 100 stocks BullEng tracks and keeps a daily
history. Writes data/uk/shorts.json.
"""

import csv
import datetime as dt
import io
import json
import pathlib
import re

import requests

CURRENT = "https://www.fca.org.uk/publication/documents/aggregated-current-net-short-positions.csv"
HISTORIC = "https://www.fca.org.uk/publication/documents/aggregated-historic-net-short-positions.csv"
UA = {"User-Agent": "BullEng research (github.com/bullengtrade/bulleng)"}
SITE = pathlib.Path("data/uk/site.json")
OUT = pathlib.Path("data/uk/shorts.json")
KEEP = 400   # days of history per company
DROP = {"plc", "group", "holdings", "holding", "the", "limited", "ltd", "sa", "ag", "nv", "se", "company",
        "co", "inc", "corporation", "corp", "international", "and", "ord", "shares", "of"}
# FCA name (normalised) -> BullEng symbol, for names that differ from the FTSE 100 list
ALIAS = {"reckittbenckiser": "RKT", "consolidatedairlines": "IAG", "jsainsbury": "SBRY",
         "howdenjoinery": "HWDN", "icg": "ICG", "lseg": "LSEG", "imperialbrands": "IMB",
         "intercontinentalhotels": "IHG", "wppplc": "WPP"}


def norm(name):
    s = str(name or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]", "", s.replace("-", "").replace(".", ""))
    return "".join(w for w in s.split() if w not in DROP)


def iso(d):
    d = (d or "").strip()
    for f in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y"):
        try:
            return dt.datetime.strptime(d[:10], f).date().isoformat()
        except ValueError:
            continue
    return None


def rows(url):
    r = requests.get(url, headers=UA, timeout=60)
    r.raise_for_status()
    rd = csv.reader(io.StringIO(r.content.decode("utf-8-sig", "replace")))
    head = [h.strip().lower() for h in next(rd)]
    col = lambda *w: next(i for i, h in enumerate(head) if all(x in h for x in w))
    ci, cn, cp, cd = col("isin"), col("name"), col("%"), col("date")
    out = []
    for r_ in rd:
        if len(r_) <= max(ci, cn, cp, cd):
            continue
        try:
            p = float(r_[cp].strip().replace("%", ""))
        except ValueError:
            continue
        d = iso(r_[cd])
        if d:
            out.append({"name": r_[cn].strip(), "isin": r_[ci].strip().upper(), "pct": p, "date": d})
    return out


def main():
    site = json.loads(SITE.read_text())
    ours = {norm(s["name"]): s for s in site["stocks"]}
    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    isin2sym = dict(old.get("isins", {}))
    maybe = []

    def match(name, isin):
        if isin in isin2sym:
            return isin2sym[isin]
        n = norm(name)
        s = ALIAS.get(n) or (ours[n]["sym"] if n in ours else None)
        if not s and len(n) >= 4:
            near = [v["sym"] for k, v in ours.items() if k[:4] == n[:4]]
            if near:
                maybe.append(f"{name} ~ {'/'.join(near)}")
        if s:
            isin2sym[isin] = s
        return s

    cur = rows(CURRENT)
    hist = {k: dict(v.get("hist", [])) for k, v in old.get("stocks", {}).items()}
    if not old:                                    # first run: load the FCA's own history
        try:
            for r in rows(HISTORIC):
                s = match(r["name"], r["isin"])
                if s:
                    hist.setdefault(s, {})[r["date"]] = r["pct"]
        except Exception as e:
            print(f"historic file skipped: {e}")

    today = dt.date.today().isoformat()
    stocks, unmatched = {}, 0
    names = {s["sym"]: s["name"] for s in site["stocks"]}
    for r in cur:
        s = match(r["name"], r["isin"])
        if not s:
            unmatched += 1
            continue
        h = hist.setdefault(s, {})
        h[r["date"]] = r["pct"]
        h[today] = r["pct"]                       # value in force today
        stocks[s] = {"name": names.get(s, r["name"]), "fca_name": r["name"], "isin": r["isin"],
                     "pct": r["pct"], "date": r["date"]}
    # companies that dropped off the current list have no reported short of 0.2%+ any more
    for s, h in hist.items():
        if s not in stocks and h:
            h[today] = 0.0
    cut = (dt.date.today() - dt.timedelta(days=KEEP)).isoformat()
    for s, h in hist.items():
        pts = sorted((d, p) for d, p in h.items() if d >= cut)
        ago = lambda n: next((p for d, p in reversed(pts) if d <= (dt.date.today() - dt.timedelta(days=n)).isoformat()), None)
        rec = stocks.setdefault(s, {"name": names.get(s, s), "pct": 0.0, "date": None})
        rec["hist"] = pts[-120:]
        rec["chg_1m"] = None if ago(30) is None else round(rec["pct"] - ago(30), 2)
    ranked = sorted((s for s in stocks if stocks[s]["pct"] > 0), key=lambda s: -stocks[s]["pct"])
    for i, s in enumerate(ranked):
        stocks[s]["rank"] = i + 1
    OUT.write_text(json.dumps({"updated": today, "companies_listed": len(cur), "shorted_ftse": len(ranked),
                               "stocks": stocks, "isins": isin2sym}, separators=(",", ":")))
    top = ", ".join(f"{s} {stocks[s]['pct']}%" for s in ranked[:5])
    print(f"FCA short positions: {len(cur)} companies listed, {len(ranked)} FTSE 100 stocks shorted "
          f"({unmatched} non-FTSE 100 skipped). Most shorted: {top}")
    if maybe:
        print("Check these near-matches (not linked):", "; ".join(sorted(set(maybe))[:30]))


if __name__ == "__main__":
    main()
