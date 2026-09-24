"""
BullEng - link each CSE company to one of the 20 CSE sectors.

Writes data/lk/sectormap.json  ->  {"AEL": "CG", "COMB": "BNK", ...}

Companies already in the map are never re-classified, so after the first run
this only touches newly listed companies. To correct a sector by hand, edit
the value in sectormap.json - the script will keep your edit.

Also prints any official sector field found on the CSE company endpoints,
so we can switch to official data if the CSE provides it.
"""

import json
import os
import pathlib
import re
import time

import requests

import tagnews  # reuse the Gemini helpers (model picking, retries)

SITE = pathlib.Path("data/lk/site.json")
OUT = pathlib.Path("data/lk/sectormap.json")
META = pathlib.Path("data/lk/sectormeta.json")   # sym -> "official" | "none" (official lookup done)
OFFICIAL_PER_RUN = 120
BATCH = 60

PROMPT = """Classify each Sri Lankan company listed on the Colombo Stock Exchange into ONE of these
S&P/CSE GICS industry groups, using its main business. Use your knowledge of the company.

Industry groups (code: name):
{sectors}

Companies (symbol: name):
{companies}

Return ONLY a JSON object mapping each symbol to a code, e.g. {{"AEL": "CG", "COMB": "BNK"}}.
Use only the codes listed above."""


def probe_official(symbol="AEL.N0000"):
    for ep in ("companyInfoSummery", "companyProfile"):
        try:
            r = requests.post(f"https://www.cse.lk/api/{ep}", data={"symbol": symbol}, timeout=30)
            text = r.text
            hits = sorted({k for k in json.loads(text).get("reqSymbolInfo", {}) if "sector" in k.lower()}) \
                if ep == "companyInfoSummery" else []
            flag = "sector" in text.lower() or "industry" in text.lower()
            print(f"  probe {ep}: sector/industry mentioned={flag} fields={hits}")
        except Exception as e:
            print(f"  probe {ep}: {e}")


def norm(s):
    return re.sub(r"[^a-z]", "", str(s).lower().replace("&", "and"))


def find_sector_strings(obj, out):
    """Collect string values of any key containing 'sector' or 'industry'."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            if ("sector" in kl or "industry" in kl) and isinstance(v, str) and v.strip() \
                    and "http" not in v and "/" not in v:
                out.append(v.strip())
            find_sector_strings(v, out)
    elif isinstance(obj, list):
        for v in obj:
            find_sector_strings(v, out)


def official_sectors(site, sectors, smap, meta):
    """Look up each company's own CSE profile for an official sector."""
    by_norm = {norm(n): c for c, n in sectors.items()}
    full = {}
    for s in site["stocks"]:
        if s["cls"] == "N":
            full.setdefault(s["sym"], s["full"])
    todo = [k for k in sorted(full) if k not in meta][:OFFICIAL_PER_RUN]
    found, unmatched = 0, set()
    for sym in todo:
        try:
            r = requests.post("https://www.cse.lk/api/companyProfile", data={"symbol": full[sym]}, timeout=30)
            strings = []
            find_sector_strings(r.json(), strings)
        except Exception:
            continue
        code = None
        for v in strings:
            nv = norm(v)
            code = by_norm.get(nv) or next((c for n, c in by_norm.items() if n and (n in nv or nv in n)), None)
            if code:
                break
            unmatched.add(v)
        meta[sym] = "official" if code else "none"
        if code:
            smap[sym] = code
            found += 1
        time.sleep(0.4)
    total = sum(v == "official" for v in meta.values())
    print(f"  official sectors: {found} found this run ({len(todo)} checked); {total} companies now use the CSE's own sector")
    if unmatched:
        print("  sector names not matched:", "; ".join(sorted(unmatched)[:10]))


def main():
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise SystemExit("GEMINI_API_KEY secret is missing.")
    site = json.loads(SITE.read_text())
    sectors = {s["code"]: s["name"] for s in site["sectors"]}
    names = {}
    for s in site["stocks"]:
        if s["cls"] in ("N", "X"):
            names.setdefault(s["sym"], s["name"])
    smap = json.loads(OUT.read_text()) if OUT.exists() else {}
    meta = json.loads(META.read_text()) if META.exists() else {}
    official_sectors(site, sectors, smap, meta)
    META.write_text(json.dumps(meta, indent=0))
    OUT.write_text(json.dumps(dict(sorted(smap.items())), indent=0))
    todo = sorted(k for k in names if k not in smap)   # AI only for companies still without a sector
    print(f"{len(names)} companies, {len(smap)} already mapped, {len(todo)} to classify")
    if not todo:
        return

    tagnews.MODELS = tagnews.pick_models(key)
    sector_list = "\n".join(f"{c}: {n}" for c, n in sectors.items())
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        companies = "\n".join(f"{k}: {names[k]}" for k in chunk)
        try:
            model, ans = tagnews.ask_gemini(key, PROMPT.format(sectors=sector_list, companies=companies))
        except Exception as e:
            print(f"  AI request failed: {e}")
            break
        got = {k: v for k, v in (ans.items() if isinstance(ans, dict) else []) if k in chunk and v in sectors}
        smap.update(got)
        print(f"  batch {i // BATCH + 1}: {len(got)}/{len(chunk)} classified (model {model})")
        time.sleep(8)

    OUT.write_text(json.dumps(dict(sorted(smap.items())), indent=0))
    counts = {}
    for v in smap.values():
        counts[sectors.get(v, v)] = counts.get(sectors.get(v, v), 0) + 1
    print("Companies per sector:", ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda x: -x[1])))


if __name__ == "__main__":
    main()
