"""
BullEng - Sri Lankan economic news collector.

Runs on GitHub Actions a few times a day. Reads public RSS news feeds and
saves each headline with its link, source and time:

  data/news/lk/YYYY-MM-DD.json  - every headline published that day (Sri Lanka time)
  data/news/lk/latest.json      - the newest 150 headlines, for the website

Only headlines and links are stored, never article text. Later, AI tagging
will add the sectors and stocks each story may affect.
"""

import datetime as dt
import email.utils
import hashlib
import html
import json
import pathlib
import re
import xml.etree.ElementTree as ET

import requests

FEEDS = [
    ("EconomyNext", "https://economynext.com/feed/"),
    ("Lanka Business Online", "https://www.lankabusinessonline.com/feed/"),
    ("Google News", "https://news.google.com/rss/search?q=%22Sri+Lanka%22+economy+OR+%22Central+Bank+of+Sri+Lanka%22"
                    "+OR+%22Colombo+Stock+Exchange%22+when:2d&hl=en-LK&gl=LK&ceid=LK:en"),
]

ROOT = pathlib.Path("data/news/lk")
SL = dt.timezone(dt.timedelta(hours=5, minutes=30))
KEEP_DAYS = 3        # only file stories from the last few days
LATEST_MAX = 150
HEADERS = {"User-Agent": "BullEng news job (github.com/bullengtrade/bulleng)"}
ATOM = "{http://www.w3.org/2005/Atom}"


def clean(s):
    s = re.sub(r"<[^>]+>", " ", html.unescape(s or ""))
    return re.sub(r"\s+", " ", s).strip()


def parse_date(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        d = email.utils.parsedate_to_datetime(s)          # RSS style
    except (TypeError, ValueError):
        try:
            d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))  # Atom style
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def parse_feed(xml_bytes):
    """Return (title, link, date, source) for each entry in an RSS or Atom feed."""
    root = ET.fromstring(xml_bytes)
    out = []
    for it in root.iter("item"):
        src = it.find("source")
        out.append((clean(it.findtext("title")), (it.findtext("link") or "").strip(),
                    parse_date(it.findtext("pubDate")), clean(src.text) if src is not None else None))
    for e in root.iter(ATOM + "entry"):
        link = e.find(ATOM + "link")
        out.append((clean(e.findtext(ATOM + "title")), link.get("href", "") if link is not None else "",
                    parse_date(e.findtext(ATOM + "published") or e.findtext(ATOM + "updated")), None))
    return out


def story_id(title):
    """Same headline from two feeds -> same id, so duplicates merge."""
    key = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def main():
    now = dt.datetime.now(SL)
    cutoff = now - dt.timedelta(days=KEEP_DAYS)
    by_day = {}

    for name, url in FEEDS:
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            entries = parse_feed(r.content)
        except Exception as e:
            print(f"  FAILED {name}: {e}")
            continue
        kept = 0
        for title, link, when, src in entries:
            if not title or not link:
                continue
            source = src or name
            if src and title.endswith(" - " + src):               # "Headline - Publisher"
                title = title[: -len(src) - 3]
            elif name == "Google News" and " - " in title:
                title, source = title.rsplit(" - ", 1)
            when = (when or now).astimezone(SL)
            if when < cutoff:
                continue
            item = {"id": story_id(title), "title": title, "link": link,
                    "source": source, "published": when.isoformat(timespec="minutes")}
            by_day.setdefault(when.date().isoformat(), {}).setdefault(item["id"], item)  # first source wins
            kept += 1
        print(f"  ok  {name:24s} {len(entries):4d} in feed, {kept:4d} recent")

    ROOT.mkdir(parents=True, exist_ok=True)
    added = 0
    for day, items in by_day.items():
        f = ROOT / f"{day}.json"
        existing = {i["id"]: i for i in json.loads(f.read_text())} if f.exists() else {}
        for k, v in items.items():
            if k not in existing:
                existing[k] = v
                added += 1
        f.write_text(json.dumps(sorted(existing.values(), key=lambda i: i["published"], reverse=True),
                                ensure_ascii=False, indent=0))

    recent = []
    for f in sorted(ROOT.glob("20*.json"))[-KEEP_DAYS - 1:]:
        recent += json.loads(f.read_text())
    recent.sort(key=lambda i: i["published"], reverse=True)
    (ROOT / "latest.json").write_text(json.dumps(
        {"updated": now.isoformat(timespec="minutes"), "items": recent[:LATEST_MAX]},
        ensure_ascii=False, indent=0))
    print(f"Added {added} new headlines; latest.json has {min(len(recent), LATEST_MAX)}")


if __name__ == "__main__":
    main()
