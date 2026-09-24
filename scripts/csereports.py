"""
BullEng - CSE company financial reports (discovery + extraction test).

1. Reads the CSE financial-report feed and keeps a permanent archive of
   report links in data/lk/reports/index.json (grows every run).
2. Probes for a per-company reports list.
3. For the test companies (env TEST_SYMBOLS, default JKH,COMB,DIAL) downloads
   the newest report PDF and asks Gemini to extract the key figures into
   data/lk/reports/extracted/<SYMBOL>.json, printing them for checking.
"""

import base64
import json
import os
import pathlib
import re
import time

import requests

import tagnews   # Gemini model picking

API = "https://www.cse.lk/api/"
CDN = "https://cdn.cse.lk/"
UA = {"User-Agent": "BullEng research (github.com/bullengtrade/bulleng)"}
ROOT = pathlib.Path("data/lk/reports")
MAX_PDF_MB = 15

EXTRACT = """You are reading an official financial report of a company listed on the Colombo Stock Exchange.
Extract figures for the GROUP (consolidated) if shown, otherwise the company. Use the CURRENT period columns.
Convert all money values to full Sri Lankan rupees (if the report says Rs '000, multiply by 1,000; if Rs Mn, by 1,000,000).
Return ONLY this JSON (null if not found, do not guess):
{"company": "", "report_type": "interim|annual", "period_end": "YYYY-MM-DD", "months_covered": 3|6|9|12,
 "units_in_report": "e.g. Rs 000", "revenue_cumulative": null, "revenue_cumulative_prior_year": null,
 "profit_attributable_to_owners_cumulative": null, "eps_cumulative": null, "eps_quarter": null,
 "net_assets_per_share": null, "equity_attributable_to_owners": null, "shares_in_issue": null,
 "operating_cash_flow_cumulative": null, "capex_cumulative": null, "dividends_per_share_paid_cumulative": null,
 "notes": "one short line on anything unusual"}"""


def post(ep, **kw):
    r = requests.post(API + ep, headers=UA, timeout=45, **kw)
    r.raise_for_status()
    return r.json()


def ms_to_str(v):
    return time.strftime("%Y-%m-%d", time.gmtime(v / 1000)) if isinstance(v, (int, float)) else str(v)


def gemini_pdf(key, pdf_bytes, prompt):
    body = {"contents": [{"parts": [{"inline_data": {"mime_type": "application/pdf",
                                                      "data": base64.b64encode(pdf_bytes).decode()}},
                                     {"text": prompt}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}}
    last = None
    for rnd in range(3):
        for model in list(tagnews.MODELS):
            try:
                r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                  params={"key": key}, json=body, timeout=180)
                if r.status_code == 404:
                    tagnews.MODELS.remove(model)
                    continue
                if r.status_code in (429, 500, 503):
                    last = f"{model}: busy ({r.status_code})"
                    continue
                r.raise_for_status()
                text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                text = re.sub(r"^```(json)?|```$", "", text.strip()).strip()
                return model, json.loads(text)
            except Exception as e:
                last = f"{model}: {e}"
        time.sleep(20 * (rnd + 1))
    raise RuntimeError(last)


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "extracted").mkdir(exist_ok=True)

    # --- company ids: report file names start with the CSE internal security id ---
    ids = {}
    try:
        cnt = requests.get(API + "cntSecurity", headers=UA, timeout=45).json()
        for c in cnt.get("content", []):
            ids[str(c["securityId"])] = c["symbol"]
        print(f"cntSecurity: {len(ids)} companies")
    except Exception as e:
        print(f"cntSecurity failed: {e}")

    # --- 1. financial report feed -> permanent archive ---
    idx_file = ROOT / "index.json"
    index = json.loads(idx_file.read_text()) if idx_file.exists() else {}
    try:
        feed = post("getFinancialAnnouncement")
        items = next((v for v in feed.values() if isinstance(v, list)), []) if isinstance(feed, dict) else feed
        print(f"feed: {len(items)} reports; fields: {sorted(items[0].keys()) if items else '-'}")
        new = 0
        for it in items:
            path = it.get("path") or ""
            if not path or path in index:
                continue
            m = re.search(r"upload_report_file/(\d+)_", path)
            sid = m.group(1) if m else None
            index[path] = {"symbol": ids.get(sid) or it.get("symbol"), "security_id": sid,
                           "title": it.get("fileText"), "date": ms_to_str(it.get("manualDate") or it.get("uploadedDate")),
                           "url": CDN + path.lstrip("/")}
            new += 1
        dates = sorted(v["date"] for v in index.values() if v.get("date"))
        print(f"archive: {len(index)} report links ({new} new), dates {dates[0] if dates else '-'} to {dates[-1] if dates else '-'}")
        for it in items[:3]:
            print("  sample:", {k: it.get(k) for k in list(it)[:8]})
    except Exception as e:
        print(f"feed failed: {e}")
    idx_file.write_text(json.dumps(index, indent=0))

    # --- 2. probe for a per-company report list ---
    per_company = {}
    for ep in ("financials", "companyFinancials", "getFinancialReports", "financialReports"):
        try:
            r = requests.post(API + ep, headers=UA, data={"symbol": "JKH.N0000"}, timeout=30)
            body = r.text[:300].replace("\n", " ")
            print(f"probe {ep}: HTTP {r.status_code} {body[:160]}")
            if r.ok and "pdf" in r.text.lower():
                per_company[ep] = r.json()
        except Exception as e:
            print(f"probe {ep}: {e}")

    # --- 3. extraction test ---
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        print("No GEMINI_API_KEY - skipping extraction test")
        return
    tagnews.MODELS = tagnews.pick_models(key)
    tests = [s.strip() for s in os.environ.get("TEST_SYMBOLS", "JKH,COMB,DIAL").split(",") if s.strip()]
    have = {v.get("symbol") for v in index.values()}
    if not any(t in have for t in tests):   # test companies not in the feed yet: use the newest reports instead
        newest = sorted([v for v in index.values() if v.get("symbol")], key=lambda v: v["date"], reverse=True)
        tests = list(dict.fromkeys(v["symbol"] for v in newest))[:3]
        print(f"Test companies not in the feed yet - testing the newest reports instead: {', '.join(tests)}")
    for sym in tests:
        reports = sorted([v for v in index.values() if v.get("symbol") == sym], key=lambda v: v["date"], reverse=True)
        if not reports:
            print(f"{sym}: no report in the archive yet (the feed may only hold recent filings)")
            continue
        rep = reports[0]
        try:
            pdf = requests.get(rep["url"], headers=UA, timeout=120).content
        except Exception as e:
            print(f"{sym}: download failed {e}")
            continue
        mb = len(pdf) / 1e6
        if mb > MAX_PDF_MB:
            print(f"{sym}: PDF is {mb:.1f} MB - too large for this test, skipped")
            continue
        try:
            model, data = gemini_pdf(key, pdf, EXTRACT)
        except Exception as e:
            print(f"{sym}: AI extraction failed {e}")
            continue
        data.update({"symbol": sym, "source_url": rep["url"], "source_title": rep["title"], "model": model})
        (ROOT / "extracted" / f"{sym}.json").write_text(json.dumps(data, indent=1))
        print(f"\n{sym} ({mb:.1f} MB, {model}): {rep['title']}")
        for k in ("period_end", "months_covered", "units_in_report", "revenue_cumulative",
                  "profit_attributable_to_owners_cumulative", "eps_cumulative", "net_assets_per_share",
                  "shares_in_issue", "operating_cash_flow_cumulative", "capex_cumulative",
                  "dividends_per_share_paid_cumulative", "notes"):
            print(f"   {k}: {data.get(k)}")
        # sanity check: EPS x shares should be close to profit
        p, e, s = data.get("profit_attributable_to_owners_cumulative"), data.get("eps_cumulative"), data.get("shares_in_issue")
        if p and e and s:
            print(f"   check EPS x shares / profit = {e * s / p:.2f} (should be about 1.00)")
        time.sleep(8)


if __name__ == "__main__":
    main()
