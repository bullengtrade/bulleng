"""
BullEng - CSE company fundamentals from official filings (free).

For each CSE company (largest first):
  1. gets its report list from the CSE (financials endpoint)
  2. reads the newest financial statements with Gemini (and the last
     full-year statements when the newest covers less than 12 months)
  3. works out trailing-12-month figures per share, keeping one-off
     gains/losses separate, and runs sanity checks

Saves data/lk/fundamentals.json for the website. Only a limited number of
PDFs are read per run (free AI limits), so the first full pass takes several
days; after that, only companies with new filings are re-read.
"""

import base64
import datetime as dt
import json
import os
import pathlib
import re
import time

import requests

import tagnews   # Gemini model picking

API, CDN = "https://www.cse.lk/api/", "https://cdn.cse.lk/"
UA = {"User-Agent": "BullEng research (github.com/bullengtrade/bulleng)"}
SITE = pathlib.Path("data/lk/site.json")
SMAP = pathlib.Path("data/lk/sectormap.json")
OUT = pathlib.Path("data/lk/fundamentals.json")
MAX_PDFS = int(os.environ.get("MAX_PDFS", "40"))
MAX_MB = 15
RECHECK_DAYS = 7

FIELDS = ["revenue", "profit_attributable_to_owners", "one_off_items_attributable", "eps",
          "operating_cash_flow", "capex", "dividends_paid"]

PROMPT = """You are reading official financial statements of a company listed on the Colombo Stock Exchange.
Use the GROUP (consolidated) figures if shown, otherwise the company. Convert all money to full Sri Lankan
rupees (Rs '000 -> x1,000; Rs Mn -> x1,000,000). EPS and per-share values stay in rupees per share.
"cumulative" = year-to-date for the CURRENT period; "prior" = the SAME period of the previous year (comparative column).
one_off_items_attributable = total of clearly one-off / non-recurring items included in profit attributable to owners
(e.g. bargain purchase gains, gains or losses on disposal of subsidiaries or property, impairments, fair value gains
on investment property, large tax reversals). Positive if they increased profit, negative if they reduced it, 0 if none.
Return ONLY this JSON (null if not found; never guess):
{"report_type": "interim|annual", "period_end": "YYYY-MM-DD", "months_covered": 3|6|9|12,
 "revenue_cumulative": null, "revenue_prior": null,
 "profit_attributable_to_owners_cumulative": null, "profit_attributable_to_owners_prior": null,
 "one_off_items_attributable_cumulative": null, "one_off_items_attributable_prior": null,
 "eps_cumulative": null, "eps_prior": null,
 "operating_cash_flow_cumulative": null, "operating_cash_flow_prior": null,
 "capex_cumulative": null, "capex_prior": null,
 "dividends_paid_cumulative": null, "dividends_paid_prior": null,
 "net_assets_per_share": null, "equity_attributable_to_owners": null, "shares_in_issue": null,
 "one_off_note": "short description of one-off items, or empty"}"""


def gemini_pdf(key, pdf):
    body = {"contents": [{"parts": [{"inline_data": {"mime_type": "application/pdf",
                                                      "data": base64.b64encode(pdf).decode()}},
                                     {"text": PROMPT}]}],
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
                return json.loads(re.sub(r"^```(json)?|```$", "", text.strip()).strip())
            except Exception as e:
                last = f"{model}: {e}"
        time.sleep(20 * (rnd + 1))
    raise RuntimeError(last)


def report_list(full_symbol):
    r = requests.post(API + "financials", headers=UA, data={"symbol": full_symbol}, timeout=45)
    r.raise_for_status()
    out = []
    for key, items in (r.json() or {}).items():
        if not isinstance(items, list):
            continue
        for it in items:
            if isinstance(it, dict) and it.get("path"):
                d = it.get("manualDate") or it.get("uploadedDate")
                out.append({"path": it["path"], "title": it.get("fileText") or "", "list": key,
                            "ms": d if isinstance(d, (int, float)) else 0})
    return sorted(out, key=lambda x: x["ms"], reverse=True)


def is_annual(rep):
    return "annual" in rep["list"].lower() or "annual report" in rep["title"].lower()


def fetch_pdf(rep):
    pdf = requests.get(CDN + rep["path"].lstrip("/"), headers=UA, timeout=120).content
    if len(pdf) > MAX_MB * 1e6 or not pdf.startswith(b"%PDF"):
        return None
    return pdf


def fy_report(reps, fy_end):
    """Find the full-year statements for the fiscal year ending fy_end (prefer small interim Q4 statements)."""
    d = dt.date.fromisoformat(fy_end)
    month, year = d.strftime("%B").lower(), str(d.year)
    pats = [f"{d.day}", month, year]
    cands = [r for r in reps if all(p in r["title"].lower() for p in (month, year))
             and (str(d.day) in r["title"] or "year" in r["title"].lower() or "annual" in r["title"].lower())]
    cands.sort(key=lambda r: (is_annual(r), -r["ms"]))   # interim Q4 first, then annual report
    return cands


def ttm(cur, fy, field):
    """Trailing 12 months: last full year + this year-to-date - same period last year.
    Falls back to the last full year, or to annualising the year-to-date figure."""
    c, p, m = cur.get(f"{field}_cumulative"), cur.get(f"{field}_prior"), cur.get("months_covered")
    if m == 12:
        return c
    f = fy.get(f"{field}_cumulative") if fy else None
    if f is not None and c is not None and p is not None:
        return f + c - p
    if f is not None:
        return f
    if c is not None and m:
        return c * 12 / m
    return None


def main():
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise SystemExit("GEMINI_API_KEY secret is missing.")
    site = json.loads(SITE.read_text())
    smap = json.loads(SMAP.read_text()) if SMAP.exists() else {}
    bank_codes = {s["code"] for s in site["sectors"] if "bank" in s["name"].lower()}
    state = json.loads(OUT.read_text()) if OUT.exists() else {"stocks": {}, "seen": {}}
    stocks, seen = state["stocks"], state["seen"]
    today = dt.date.today()

    universe = [s for s in site["stocks"] if s["cls"] == "N" and s.get("price")]
    # never-seen companies first (largest first), then the ones checked longest ago
    universe.sort(key=lambda s: (s["sym"] in seen, seen.get(s["sym"], {}).get("checked", ""), -(s.get("mcap") or 0)))
    tagnews.MODELS = tagnews.pick_models(key)

    pdfs_read, updated, problems = 0, 0, []
    for s in universe:
        if pdfs_read >= MAX_PDFS:
            break
        sym, full = s["sym"], s["full"]
        last = seen.get(sym, {})
        if last.get("checked") and (today - dt.date.fromisoformat(last["checked"])).days < RECHECK_DAYS:
            continue
        try:
            reps = report_list(full)
        except Exception as e:
            problems.append(f"{sym}: report list failed ({e})")
            continue
        time.sleep(0.5)
        latest = next((r for r in reps if not is_annual(r)), None) or (reps[0] if reps else None)
        seen[sym] = {"checked": today.isoformat(), "latest": latest["path"] if latest else None}
        if not latest:
            problems.append(f"{sym}: no reports listed")
            continue
        if last.get("latest") == latest["path"] and sym in stocks:
            continue                                   # nothing new filed

        try:
            pdf = fetch_pdf(latest)
            if pdf is None:
                problems.append(f"{sym}: latest report too large or not a PDF")
                continue
            cur = gemini_pdf(key, pdf)
            pdfs_read += 1
            time.sleep(6)
            fy = None
            if cur.get("months_covered") and cur["months_covered"] < 12 and cur.get("period_end"):
                pe = dt.date.fromisoformat(cur["period_end"])
                m = pe.month - cur["months_covered"]
                y = pe.year + (m - 1) // 12
                m = (m - 1) % 12 + 1
                fy_end = (dt.date(y, m % 12 + 1, 1) - dt.timedelta(days=1)) if m != 12 else dt.date(y, 12, 31)
                for rep in fy_report(reps, fy_end.isoformat())[:2]:
                    if pdfs_read >= MAX_PDFS:
                        break
                    pdf2 = fetch_pdf(rep)
                    if pdf2 is None:
                        continue
                    cand = gemini_pdf(key, pdf2)
                    pdfs_read += 1
                    time.sleep(6)
                    if cand.get("months_covered") == 12:
                        fy = cand
                        break
        except Exception as e:
            problems.append(f"{sym}: extraction failed ({e})")
            continue

        shares = cur.get("shares_in_issue")
        if not shares and s.get("mcap") and s.get("price"):
            shares = s["mcap"] / s["price"]           # banks often keep the share count in the notes
        eps = ttm(cur, fy, "eps")
        profit = ttm(cur, fy, "profit_attributable_to_owners")
        if eps is None and profit is not None and shares:
            eps = profit / shares
        pe_date = cur.get("period_end")
        if pe_date and (today - dt.date.fromisoformat(pe_date)).days > 460:
            problems.append(f"{sym}: newest report found is for {pe_date} - too old, skipped")
            stocks.pop(sym, None)
            continue
        one_off = ttm(cur, fy, "one_off_items_attributable") or 0
        ocf, capex = ttm(cur, fy, "operating_cash_flow"), ttm(cur, fy, "capex")
        divs = ttm(cur, fy, "dividends_paid")
        flags = []
        if not shares or eps is None:
            flags.append("missing EPS or share count")
        else:
            if profit and abs(eps * shares / profit - 1) > 0.2:
                flags.append("EPS x shares does not match profit")
            if s.get("mcap") and not 0.33 < s["price"] * shares / s["mcap"] < 3:
                flags.append("share count does not match market cap")
            if eps > 0 and not 1 <= s["price"] / eps <= 1000:
                flags.append("P/E outside 1-1000")
        if flags:
            problems.append(f"{sym}: " + "; ".join(flags))
            stocks.pop(sym, None)
            continue

        underlying = (profit - one_off) / shares if profit is not None else eps
        rc, rp = cur.get("revenue_cumulative"), cur.get("revenue_prior")
        growth = (rc / rp - 1) if rc and rp and rp > 0 else None
        nav = cur.get("net_assets_per_share") or (cur["equity_attributable_to_owners"] / shares
                                                   if cur.get("equity_attributable_to_owners") else None)
        is_bank = smap.get(sym) in bank_codes
        stocks[sym] = {
            "period_end": cur.get("period_end"), "months_covered": cur.get("months_covered"),
            "ttm": cur.get("months_covered") == 12 or fy is not None,   # False = annualised year-to-date
            "eps": round(underlying, 4), "eps_reported": round(eps, 4),
            "bvps": round(nav, 4) if nav else None,
            "dps": round(divs / shares, 4) if divs and divs > 0 else 0,
            "fcfps": None if is_bank or ocf is None else round((ocf - (capex or 0)) / shares, 4),
            "growth": None if growth is None else round(max(0.0, min(0.15, growth)), 4),
            "rev_cagr": None if growth is None else round(growth, 4),
            "bank": is_bank, "one_off_note": cur.get("one_off_note") or "",
            "source_url": CDN + latest["path"].lstrip("/"), "source_title": latest["title"],
        }
        updated += 1
        print(f"  {sym}: {cur.get('period_end')} ({cur.get('months_covered')}m{' + FY' if fy else ''}) "
              f"EPS {underlying:.2f} (reported {eps:.2f}), NAV {nav}, FCF/sh {stocks[sym]['fcfps']}")

    state["updated"] = today.isoformat()
    state["source"] = "CSE filings (interim and annual financial statements)"
    OUT.write_text(json.dumps(state, indent=0))
    done = len(stocks)
    print(f"\nRead {pdfs_read} PDFs, updated {updated} companies. Valuations available for {done} of {len(universe)} companies.")
    for p in problems:
        print("  " + p)


if __name__ == "__main__":
    main()
