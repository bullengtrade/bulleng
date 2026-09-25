"""
BullEng - CSE dividend announcements and financial report filings.

Runs after each CSE close. Keeps permanent archives (the CSE feeds only show
recent items) and writes the files the website reads:

  data/lk/dividends.json   - cash / scrip dividend announcements
  data/lk/filings.json     - financial reports filed (for the earnings tracker)
"""

import datetime as dt
import json
import pathlib
import re

import requests

API, CDN = "https://www.cse.lk/api/", "https://cdn.cse.lk/"
UA = {"User-Agent": "BullEng research (github.com/bullengtrade/bulleng)"}
ROOT = pathlib.Path("data/lk")
SL = dt.timezone(dt.timedelta(hours=5, minutes=30))


def post(ep):
    r = requests.post(API + ep, headers=UA, timeout=45)
    r.raise_for_status()
    body = r.json()
    return next((v for v in body.values() if isinstance(v, list)), []) if isinstance(body, dict) else body


def to_date(v):
    """CSE dates come as epoch milliseconds or strings like '21 May 2026' / '2026-05-21'."""
    if isinstance(v, (int, float)) and v > 1e11:
        return dt.datetime.fromtimestamp(v / 1000, SL).date().isoformat()
    if isinstance(v, str):
        for fmt in ("%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y", "%d-%b-%Y", "%d/%b/%Y"):
            try:
                return dt.datetime.strptime(v.strip()[:11].strip(), fmt).date().isoformat()
            except ValueError:
                continue
        m = re.search(r"\d{1,2} \w{3,9} \d{4}", v)
        if m:
            return to_date(m.group(0))
    return None


def amount(item):
    """Dividend per share, if any field carries it."""
    for k, v in item.items():
        kl = k.lower()
        if any(w in kl for w in ("amount", "rate", "pershare", "dps", "dividend")) and "date" not in kl:
            try:
                x = float(str(v).replace(",", "").replace("Rs", "").strip())
                if 0 < x < 100000:
                    return x
            except (TypeError, ValueError):
                continue
    return None


def main():
    today = dt.datetime.now(SL).date()

    # ---- dividends ----
    df = ROOT / "dividends.json"
    divs = json.loads(df.read_text()) if df.exists() else {}
    try:
        ann = post("approvedAnnouncement")
        cats = {}
        for a in ann:
            cat = str(a.get("announcementCategory") or "")
            cats[cat] = cats.get(cat, 0) + 1
            if "DIVIDEND" not in cat.upper():
                continue
            key = str(a.get("announcementId") or a.get("id"))
            if key in divs:
                continue
            divs[key] = {
                "symbol": a.get("symbol"), "company": a.get("company"), "type": cat.title(),
                "announced": to_date(a.get("dateOfAnnouncement") or a.get("createdDate")),
                "record_date": to_date(a.get("recordDate")),
                "xd_date": to_date(a.get("xdDate") or a.get("exDate") or a.get("xd")),
                "payment_date": to_date(a.get("paymentDate") or a.get("payDate")),
                "per_share": amount(a),
                "raw_fields": sorted(a.keys()),
            }
        print(f"announcements: {len(ann)} today; categories: {dict(sorted(cats.items(), key=lambda x: -x[1])[:8])}")
        sample = next((a for a in ann if "DIVIDEND" in str(a.get("announcementCategory", "")).upper()), None)
        if sample:
            print("  dividend sample:", {k: sample[k] for k in list(sample)[:14]})
    except Exception as e:
        print(f"announcements failed: {e}")
    cut = (today - dt.timedelta(days=400)).isoformat()
    divs = {k: v for k, v in divs.items() if (v.get("announced") or "9999") >= cut}
    df.write_text(json.dumps(divs, indent=0))
    upcoming = [v for v in divs.values() if (v.get("record_date") or v.get("xd_date") or "") >= today.isoformat()]
    print(f"dividends archive: {len(divs)}, upcoming: {len(upcoming)}")

    # ---- financial report filings ----
    ff = ROOT / "filings.json"
    filings = json.loads(ff.read_text()) if ff.exists() else {}
    try:
        items = post("getFinancialAnnouncement")
        new = 0
        for it in items:
            path = it.get("path") or ""
            if not path or path in filings:
                continue
            filings[path] = {"symbol": it.get("symbol"), "company": it.get("name"),
                             "title": it.get("fileText"), "url": CDN + path.lstrip("/"),
                             "filed": to_date(it.get("uploadedDate")) or to_date(it.get("manualDate")),
                             "period": to_date(it.get("manualDate"))}
            new += 1
        print(f"filings: {len(items)} in feed, {new} new, {len(filings)} in archive")
    except Exception as e:
        print(f"filings failed: {e}")
    cut = (today - dt.timedelta(days=120)).isoformat()
    filings = {k: v for k, v in filings.items() if (v.get("filed") or "9999") >= cut}
    ff.write_text(json.dumps(filings, indent=0))


if __name__ == "__main__":
    main()
