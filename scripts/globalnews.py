"""
BullEng - US and UK economic news with AI tagging.

Collects headlines (links only, never article text) from public RSS feeds,
then asks Gemini which are relevant and which sectors / tracked stocks they
may affect. Writes, for each market:

  data/news/us/latest.json, tags.json, tagged.json
  data/news/uk/latest.json, tags.json, tagged.json
"""

import datetime as dt
import json
import os
import pathlib
import time

import requests

import tagnews                                  # Gemini helpers
from fetch_news import parse_feed, story_id     # feed reading from the Sri Lanka collector

UA = {"User-Agent": "BullEng news job (github.com/bullengtrade/bulleng)"}
KEEP_DAYS, LATEST_MAX, SHOW, BATCH, MAX_BATCHES = 3, 150, 60, 20, 3

MARKETS = {
    "us": {
        "place": "the United States",
        "feeds": [
            ("Google News", "https://news.google.com/rss/search?q=%22US+economy%22+OR+%22Federal+Reserve%22+OR+%22Wall+Street%22"
                            "+OR+%22S%26P+500%22+when:1d&hl=en-US&gl=US&ceid=US:en"),
            ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
            ("CNBC Economy", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
        ],
    },
    "uk": {
        "place": "the United Kingdom",
        "feeds": [
            ("Google News", "https://news.google.com/rss/search?q=%22UK+economy%22+OR+%22Bank+of+England%22+OR+%22FTSE+100%22"
                            "+when:1d&hl=en-GB&gl=GB&ceid=GB:en"),
            ("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
            ("The Guardian", "https://www.theguardian.com/uk/business/rss"),
        ],
    },
}

PROMPT = """You tag economic news for BullEng, a free market information site covering {place}. Be strict.
A headline is RELEVANT only if it is mainly about {place}'s economy, interest rates, inflation, government
finances, trade, markets, or a company listed there, and could plausibly move share prices.
NOT relevant: sport, celebrity, crime without a business angle, politics without economic policy content,
lifestyle, or other countries' economies. When unsure, mark it NOT relevant.
Only use the headline; do not invent facts. "why" = one plain sentence (max 25 words) in your own words.

Sectors (code: name):
{sectors}

Tracked stocks (symbol: company):
{stocks}

Return ONLY a JSON list, one object per headline:
[{{"id": "...", "relevant": true, "why": "...", "impact": "pos|neg|mixed",
   "sectors": [{{"code": "...", "dir": "pos|neg"}}], "stocks": [{{"sym": "...", "dir": "pos|neg"}}]}}]
Use only codes and symbols from the lists. Irrelevant headlines: {{"id": "...", "relevant": false}}.

Headlines:
{headlines}"""


def collect(m, cfg, now):
    root = pathlib.Path(f"data/news/{m}")
    root.mkdir(parents=True, exist_ok=True)
    cutoff = now - dt.timedelta(days=KEEP_DAYS)
    by_day = {}
    for name, url in cfg["feeds"]:
        try:
            r = requests.get(url, headers=UA, timeout=30)
            r.raise_for_status()
            entries = parse_feed(r.content)
        except Exception as e:
            print(f"  {m.upper()} FAILED {name}: {e}")
            continue
        kept = 0
        for title, link, when, src in entries:
            if not title or not link:
                continue
            source = src or name
            if src and title.endswith(" - " + src):
                title = title[: -len(src) - 3]
            elif name == "Google News" and " - " in title:
                title, source = title.rsplit(" - ", 1)
            when = (when or now).astimezone(dt.timezone.utc)
            if when < cutoff:
                continue
            item = {"id": story_id(title), "title": title, "link": link, "source": source,
                    "published": when.isoformat(timespec="minutes")}
            by_day.setdefault(when.date().isoformat(), {}).setdefault(item["id"], item)
            kept += 1
        print(f"  {m.upper()} ok {name:14s} {len(entries):4d} in feed, {kept:4d} recent")
    for day, items in by_day.items():
        f = root / f"{day}.json"
        old = {i["id"]: i for i in json.loads(f.read_text())} if f.exists() else {}
        for k, v in items.items():
            old.setdefault(k, v)
        f.write_text(json.dumps(sorted(old.values(), key=lambda i: i["published"], reverse=True), indent=0))
    recent = []
    for f in sorted(root.glob("20*.json"))[-KEEP_DAYS - 1:]:
        recent += json.loads(f.read_text())
    recent.sort(key=lambda i: i["published"], reverse=True)
    (root / "latest.json").write_text(json.dumps({"updated": now.isoformat(timespec="minutes"),
                                                   "items": recent[:LATEST_MAX]}, indent=0))
    return root, recent[:LATEST_MAX]


def tag(m, cfg, root, latest, key):
    site = json.loads(pathlib.Path(f"data/{m}/site.json").read_text())
    sectors = {s["code"]: s["name"] for s in site["sectors"]}
    stocks = {s["sym"]: s["name"] for s in site["stocks"]}
    tf = root / "tags.json"
    tags = json.loads(tf.read_text()) if tf.exists() else {}
    todo = [i for i in latest if i["id"] not in tags]
    print(f"  {m.upper()}: {len(latest)} headlines, {len(todo)} to tag")
    for b in range(min(MAX_BATCHES, (len(todo) + BATCH - 1) // BATCH)):
        chunk = todo[b * BATCH:(b + 1) * BATCH]
        prompt = PROMPT.format(place=cfg["place"],
                               sectors="\n".join(f"{c}: {n}" for c, n in sectors.items()),
                               stocks="\n".join(f"{k}: {v}" for k, v in stocks.items()),
                               headlines="\n".join(f'{i["id"]}: {i["title"]} ({i["source"]})' for i in chunk))
        try:
            model, ans = tagnews.ask_gemini(key, prompt)
        except Exception as e:
            print(f"  {m.upper()} AI request failed: {e}")
            break
        ids, rel = {i["id"] for i in chunk}, 0
        for a in ans if isinstance(ans, list) else []:
            if a.get("id") not in ids:
                continue
            if not a.get("relevant"):
                tags[a["id"]] = {"relevant": False}
                continue
            rel += 1
            tags[a["id"]] = {
                "relevant": True, "why": str(a.get("why", ""))[:220],
                "impact": a.get("impact") if a.get("impact") in ("pos", "neg", "mixed") else "mixed",
                "sectors": [{"code": x["code"], "dir": "neg" if x.get("dir") == "neg" else "pos"}
                            for x in a.get("sectors", []) if isinstance(x, dict) and x.get("code") in sectors][:4],
                "stocks": [{"sym": x["sym"], "dir": "neg" if x.get("dir") == "neg" else "pos"}
                           for x in a.get("stocks", []) if isinstance(x, dict) and x.get("sym") in stocks][:5],
            }
        print(f"  {m.upper()} batch {b + 1}: {len(chunk)} -> {rel} relevant ({model})")
        time.sleep(8)
    tf.write_text(json.dumps(tags, indent=0))
    shown = [{**i, **{k: v for k, v in tags[i["id"]].items() if k != "relevant"}}
             for i in latest if tags.get(i["id"], {}).get("relevant")]
    (root / "tagged.json").write_text(json.dumps({"items": shown[:SHOW]}, indent=0))
    print(f"  {m.upper()}: tagged.json has {len(shown[:SHOW])} relevant stories")


def main():
    key = os.environ.get("GEMINI_API_KEY")
    now = dt.datetime.now(dt.timezone.utc)
    if key:
        tagnews.MODELS = tagnews.pick_models(key)
    for m, cfg in MARKETS.items():
        root, latest = collect(m, cfg, now)
        if key:
            tag(m, cfg, root, latest, key)


if __name__ == "__main__":
    main()
