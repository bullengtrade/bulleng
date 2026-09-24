"""
BullEng - AI news tagging with the free Google Gemini API.

For each new headline in data/news/lk/latest.json, Gemini decides:
  - is it relevant to Sri Lanka's economy or listed companies?
  - a one-line "why it matters" in its own words
  - likely impact (pos / neg / mixed) and which CSE sectors and stocks it may affect

Results are cached in data/news/lk/tags.json (each headline is tagged once)
and the website reads data/news/lk/tagged.json.

Needs the GitHub secret GEMINI_API_KEY.
"""

import json
import os
import pathlib
import re
import time

import requests

NEWS = pathlib.Path("data/news/lk")
SITE = pathlib.Path("data/lk/site.json")
FALLBACK_MODELS = ["gemini-flash-latest", "gemini-2.5-flash", "gemini-2.5-flash-lite"]
SKIP = ("image", "tts", "audio", "live", "embed", "vision", "exp", "thinking", "native", "robotics", "computer")
BATCH = 20          # headlines per AI request
MODELS = []
MAX_BATCHES = 4     # per run, to stay well inside the free tier
SHOW = 60           # tagged stories kept for the website

PROMPT = """You tag Sri Lankan economic news for BullEng, a free market information site.
For each headline decide if it is relevant to Sri Lanka's economy, markets or CSE-listed companies.
Only use the headline; do not invent facts. Write "why" as one plain sentence (max 25 words) in your own words
explaining why investors might care. Impact is the likely direction for the affected sectors overall.

CSE sectors (code: name):
{sectors}

CSE stocks (symbol: company):
{stocks}

Return ONLY a JSON list, one object per headline, exactly:
[{{"id": "...", "relevant": true, "why": "...", "impact": "pos|neg|mixed",
   "sectors": [{{"code": "BNK", "dir": "pos|neg"}}], "stocks": [{{"sym": "COMB", "dir": "pos|neg"}}]}}]
Use only codes and symbols from the lists above. Use empty lists if none clearly apply.
For irrelevant headlines return {{"id": "...", "relevant": false}}.

Headlines:
{headlines}"""


def pick_models(key):
    """Ask Google which models this key can use and prefer the newest 'flash' model."""
    try:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                         params={"key": key, "pageSize": 200}, timeout=30)
        r.raise_for_status()
        found = []
        for m in r.json().get("models", []):
            name = m.get("name", "").split("/")[-1]
            if "generateContent" not in m.get("supportedGenerationMethods", []):
                continue
            if "flash" not in name or any(s in name for s in SKIP):
                continue
            v = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
            score = (float(v.group(1)) if v else 0) - (0.3 if "lite" in name else 0) - (0.2 if "preview" in name else 0)
            found.append((score, name))
        names = [n for _, n in sorted(found, reverse=True)]
        print("  models available:", ", ".join(names[:6]) or "none")
    except Exception as e:
        print(f"  could not list models ({e}); using defaults")
        names = []
    chosen = ([os.environ["GEMINI_MODEL"]] if os.environ.get("GEMINI_MODEL") else []) + names[:3]
    return chosen + [m for m in FALLBACK_MODELS if m not in chosen]


def ask_gemini(key, prompt):
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}}
    last = None
    for model in MODELS:  # set in main() from pick_models()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            r = requests.post(url, params={"key": key}, json=body, timeout=90)
            if r.status_code in (404, 429, 503):  # unavailable or busy - try the next model
                last = f"{model}: HTTP {r.status_code} {r.text[:150]}"
                print(f"  {last}")
                continue
            r.raise_for_status()
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            text = re.sub(r"^```(json)?|```$", "", text.strip()).strip()
            return model, json.loads(text)
        except Exception as e:
            last = f"{model}: {e}"
    raise RuntimeError(last)


def main():
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise SystemExit("GEMINI_API_KEY secret is missing - add it in GitHub settings.")

    latest = json.loads((NEWS / "latest.json").read_text())["items"]
    site = json.loads(SITE.read_text())
    sectors = {s["code"]: s["name"] for s in site["sectors"]}
    stocks = {}
    for s in site["stocks"]:
        if s["cls"] == "N":
            stocks.setdefault(s["sym"], s)
    global MODELS
    MODELS = pick_models(key)
    tags_file = NEWS / "tags.json"
    tags = json.loads(tags_file.read_text()) if tags_file.exists() else {}

    todo = [i for i in latest if i["id"] not in tags]
    print(f"{len(latest)} headlines, {len(todo)} not yet tagged")
    sector_list = "\n".join(f"{c}: {n}" for c, n in sectors.items())
    stock_list = "\n".join(f"{k}: {v['name']}" for k, v in sorted(stocks.items()))

    for b in range(min(MAX_BATCHES, (len(todo) + BATCH - 1) // BATCH)):
        chunk = todo[b * BATCH:(b + 1) * BATCH]
        heads = "\n".join(f'{i["id"]}: {i["title"]} ({i["source"]})' for i in chunk)
        try:
            model, answer = ask_gemini(key, PROMPT.format(sectors=sector_list, stocks=stock_list, headlines=heads))
        except Exception as e:
            print(f"  AI request failed: {e}")
            break
        ids = {i["id"] for i in chunk}
        n_rel = 0
        for a in answer if isinstance(answer, list) else []:
            if a.get("id") not in ids:
                continue
            if not a.get("relevant"):
                tags[a["id"]] = {"relevant": False}
                continue
            n_rel += 1
            tags[a["id"]] = {
                "relevant": True,
                "why": str(a.get("why", ""))[:220],
                "impact": a.get("impact") if a.get("impact") in ("pos", "neg", "mixed") else "mixed",
                "sectors": [{"code": x["code"], "dir": "neg" if x.get("dir") == "neg" else "pos"}
                            for x in a.get("sectors", []) if isinstance(x, dict) and x.get("code") in sectors][:4],
                "stocks": [{"sym": stocks[x["sym"]]["full"], "dir": "neg" if x.get("dir") == "neg" else "pos"}
                           for x in a.get("stocks", []) if isinstance(x, dict) and x.get("sym") in stocks][:5],
            }
        print(f"  batch {b + 1}: {len(chunk)} headlines -> {n_rel} relevant (model {model})")
        time.sleep(8)  # stay under the free tier's requests-per-minute limit

    tags_file.write_text(json.dumps(tags, ensure_ascii=False, indent=0))

    shown = []
    for i in latest:
        t = tags.get(i["id"])
        if t and t.get("relevant"):
            shown.append({**i, **{k: v for k, v in t.items() if k != "relevant"}})
    (NEWS / "tagged.json").write_text(json.dumps(
        {"updated": json.loads((NEWS / "latest.json").read_text())["updated"], "items": shown[:SHOW]},
        ensure_ascii=False, indent=0))
    print(f"tagged.json: {len(shown[:SHOW])} relevant stories")
    by_src = {}
    for i in latest:
        by_src[i["source"]] = by_src.get(i["source"], 0) + 1
    print("Headlines by source:", ", ".join(f"{k} {v}" for k, v in sorted(by_src.items(), key=lambda x: -x[1])))


if __name__ == "__main__":
    main()
