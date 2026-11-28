"""
BullEng - US insider trades (SEC Form 4), free from SEC EDGAR.

Directors, executives and 10%+ owners must report their own trades in their
company's shares within two business days. This script reads the SEC's daily
index of filings, keeps the Form 4s for companies BullEng tracks, and records
open-market purchases (code P) and sales (code S). Awards, option exercises,
gifts and tax withholding are left out because they are not decisions to buy
or sell at the market price.

Writes data/us/insiders.json (last 120 days of trades plus a 90-day summary).
"""

import datetime as dt
import json
import pathlib
import re
import time
import xml.etree.ElementTree as ET

import requests

HEADERS = {"User-Agent": "BullEng research bullengtrade@gmail.com"}   # the SEC asks for a contact
SITE = pathlib.Path("data/us/site.json")
OUT = pathlib.Path("data/us/insiders.json")
KEEP_DAYS, SUMMARY_DAYS = 120, 90
LOOKBACK = 35            # business days of index files to check (fills gaps after missed runs)
MAX_FILINGS = 900        # per run, to stay well inside the SEC's 10 requests/second limit
PAUSE = 0.13


def get(url):
    for i in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code in (403, 404):
                return None
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 + 3 * i)
                continue
            r.raise_for_status()
            return r.text
        except requests.RequestException:
            time.sleep(2 + 3 * i)
    return None


def index_lines(day):
    q = (day.month - 1) // 3 + 1
    txt = get(f"https://www.sec.gov/Archives/edgar/daily-index/{day.year}/QTR{q}/form.{day:%Y%m%d}.idx")
    if txt is None:
        return None
    rows = []
    for line in txt.splitlines():
        if not line.startswith("4 "):            # exact form "4" (not 4/A, 40-F ...)
            continue
        parts = line.split()
        if len(parts) < 5 or not parts[-1].startswith("edgar/"):
            continue
        rows.append((parts[-3], parts[-1]))      # (cik, file path)
    return rows


def val(node, path):
    x = node.find(path)
    if x is None:
        return None
    v = x.find("value")
    t = (v.text if v is not None else x.text) or ""
    return t.strip() or None


def num(s):
    try:
        return float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return None


def parse(text):
    m = re.search(r"<ownershipDocument>.*?</ownershipDocument>", text, re.S)
    if not m:
        return None
    root = ET.fromstring(m.group(0))
    owners = []
    for ro in root.findall("reportingOwner"):
        name = (ro.findtext("reportingOwnerId/rptOwnerName") or "").strip()
        rel = ro.find("reportingOwnerRelationship")
        role = []
        if rel is not None:
            flag = lambda k: (rel.findtext(k) or "").strip().lower() in ("1", "true")
            if flag("isOfficer"):
                role.append((rel.findtext("officerTitle") or "Officer").strip())
            if flag("isDirector"):
                role.append("Director")
            if flag("isTenPercentOwner"):
                role.append("10% owner")
            if flag("isOther"):
                role.append((rel.findtext("otherText") or "Other").strip())
        owners.append((name, ", ".join(dict.fromkeys(r for r in role if r)) or "Insider"))
    plan = (root.findtext("aff10b5One") or "").strip().lower() in ("1", "true")
    trades = {}
    for t in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = val(t, "transactionCoding/transactionCode")
        if code not in ("P", "S"):
            continue
        sh, px = num(val(t, "transactionAmounts/transactionShares")), num(val(t, "transactionAmounts/transactionPricePerShare"))
        if not sh:
            continue
        g = trades.setdefault(code, {"shares": 0.0, "value": 0.0, "priced": 0.0, "date": None, "after": None})
        g["shares"] += sh
        if px:
            g["value"] += sh * px
            g["priced"] += sh
        d = val(t, "transactionDate")
        if d and (g["date"] is None or d[:10] > g["date"]):
            g["date"] = d[:10]
        after = num(val(t, "postTransactionAmounts/sharesOwnedFollowingTransaction"))
        if after is not None and (val(t, "ownershipNature/directOrIndirectOwnership") or "D") == "D":
            g["after"] = after
    return {"symbol": (root.findtext("issuer/issuerTradingSymbol") or "").strip().upper(),
            "owners": owners, "plan": plan, "trades": trades}


def business_days(n):
    d, out = dt.date.today(), []
    while len(out) < n:
        d -= dt.timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return out


def main():
    site = json.loads(SITE.read_text())
    ours = {s["sym"].upper() for s in site["stocks"]}
    names = {s["sym"].upper(): s["name"] for s in site["stocks"]}
    tick = requests.get("https://www.sec.gov/files/company_tickers.json", headers=HEADERS, timeout=30).json()
    cik2sym = {}
    for v in tick.values():
        sym = v["ticker"].upper().replace(".", "-")
        if sym in ours and str(v["cik_str"]) not in cik2sym:
            cik2sym[str(v["cik_str"])] = sym

    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    done = set(old.get("days_done", []))
    trades = {t["id"]: t for t in old.get("trades", [])}
    fetched, failed_days = 0, []

    for day in sorted(business_days(LOOKBACK)):
        iso = day.isoformat()
        if iso in done or fetched >= MAX_FILINGS:
            continue
        rows = index_lines(day)
        time.sleep(PAUSE)
        if rows is None:                 # holiday, or the index is not published yet
            failed_days.append(iso)
            continue
        files = {}
        for cik, path in rows:
            if cik in cik2sym:
                files[path] = cik2sym[cik]
        complete = True
        for path, sym in files.items():
            acc = path.rsplit("/", 1)[-1].replace(".txt", "")
            if any(k.startswith(acc) for k in trades):
                continue
            if fetched >= MAX_FILINGS:
                complete = False
                break
            text = get("https://www.sec.gov/Archives/" + path)
            fetched += 1
            time.sleep(PAUSE)
            if not text:
                continue
            try:
                doc = parse(text)
            except ET.ParseError:
                continue
            if not doc or not doc["trades"]:
                continue
            issuer = doc["symbol"].replace(".", "-") or sym
            if issuer not in ours:
                issuer = sym
            owner, role = doc["owners"][0] if doc["owners"] else ("Unknown", "Insider")
            if len(doc["owners"]) > 1:
                owner += f" and {len(doc['owners']) - 1} other" + ("s" if len(doc["owners"]) > 2 else "")
            cik_dir = path.split("/")[2]
            url = f"https://www.sec.gov/Archives/edgar/data/{cik_dir}/{acc.replace('-', '')}/{acc}-index.htm"
            for code, g in doc["trades"].items():
                trades[f"{acc}:{code}"] = {
                    "id": f"{acc}:{code}", "sym": issuer, "owner": owner.title() if owner.isupper() else owner,
                    "role": role, "side": "buy" if code == "P" else "sell", "date": g["date"] or iso, "filed": iso,
                    "shares": round(g["shares"]), "price": round(g["value"] / g["priced"], 2) if g["priced"] else None,
                    "value": round(g["value"]) if g["value"] else None,
                    "owned_after": round(g["after"]) if g["after"] is not None else None,
                    "plan": doc["plan"], "url": url}
        if complete:
            done.add(iso)

    cut = (dt.date.today() - dt.timedelta(days=KEEP_DAYS)).isoformat()
    tl = sorted((t for t in trades.values() if t["filed"] >= cut), key=lambda t: (t["filed"], t["value"] or 0), reverse=True)
    scut = (dt.date.today() - dt.timedelta(days=SUMMARY_DAYS)).isoformat()
    summary = {}
    for t in tl:
        if t["date"] < scut:
            continue
        s = summary.setdefault(t["sym"], {"name": names.get(t["sym"], t["sym"]), "buys": 0, "sells": 0,
                                          "buy_value": 0, "sell_value": 0, "buyers": [], "sellers": []})
        k = "buy" if t["side"] == "buy" else "sell"
        s[k + "s"] += 1
        s[k + "_value"] += t["value"] or 0
        who = s[k + "ers"]
        if t["owner"] not in who:
            who.append(t["owner"])
    for s in summary.values():
        s["buyers"], s["sellers"] = len(s["buyers"]), len(s["sellers"])

    OUT.write_text(json.dumps({"updated": dt.date.today().isoformat(),
                               "days_done": sorted(d for d in done if d >= cut),
                               "trades": tl, "summary": summary}, separators=(",", ":")))
    buys = sum(1 for t in tl if t["side"] == "buy")
    print(f"Insider trades: {len(tl)} kept ({buys} buys, {len(tl) - buys} sells); "
          f"{fetched} filings read this run; {len(done)} days complete; "
          f"index not available for: {', '.join(failed_days[-5:]) or 'none'}")


if __name__ == "__main__":
    main()
