#!/usr/bin/env python3
"""Update static TübingenTalks JSON feeds.

The scraper is intentionally dependency-free for GitHub Actions. It supports RSS
sources today and keeps the existing checked-in sample feed as a fallback while
the individual HTML extractors are tightened source by source.
"""

from __future__ import annotations

import datetime as dt
import email.utils
import hashlib
import html
import json
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SOURCES_PATH = DATA_DIR / "sources.json"
EVENTS_PATH = DATA_DIR / "events.json"
TIMEZONE = "Europe/Berlin"
BERLIN = dt.timezone(dt.timedelta(hours=2))


def main() -> int:
    sources_feed = read_json(SOURCES_PATH)
    existing_feed = read_json(EVENTS_PATH) if EVENTS_PATH.exists() else {"events": []}
    sources = [source for source in sources_feed.get("sources", []) if source.get("selectable", True)]

    scraped_events = []
    for source in sources:
      try:
        scraped_events.extend(scrape_source(source))
      except Exception as exc:
        print(f"warning: failed to scrape {source.get('id')}: {exc}", file=sys.stderr)

    events = scraped_events or existing_feed.get("events", [])
    events = normalize_events(events, sources)

    generated_at = dt.datetime.now(BERLIN).isoformat(timespec="seconds")
    output = {
        "version": generated_at,
        "generatedAt": generated_at,
        "events": events,
    }

    write_json_if_changed(EVENTS_PATH, output)

    sources_feed["generatedAt"] = generated_at
    write_json_if_changed(SOURCES_PATH, sources_feed)
    return 0


def scrape_source(source: dict) -> list[dict]:
    if source.get("rssUrl"):
        return scrape_rss(source)
    return []


def scrape_rss(source: dict) -> list[dict]:
    raw = fetch_text(source["rssUrl"])
    root = ET.fromstring(raw)
    events = []
    for item in root.findall(".//item"):
        title = text_of(item, "title")
        link = text_of(item, "link") or source["url"]
        description = strip_html(text_of(item, "description"))
        published = parse_date(text_of(item, "pubDate")) or dt.datetime.now(BERLIN)
        start = infer_start(description) or published
        end = start + dt.timedelta(hours=1)
        events.append(
            {
                "id": stable_id(source["id"], link, title, start.isoformat(), ""),
                "title": title or "Untitled event",
                "speaker": source["name"],
                "abstract": description,
                "sourceId": source["id"],
                "sourceName": source["name"],
                "sourceUrl": source["url"],
                "eventUrl": link,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "timezone": TIMEZONE,
                "location": infer_location(description),
                "registrationRequired": "registration" in description.lower() or "anmeldung" in description.lower(),
                "registrationUrl": link,
                "topics": infer_topics(title, description, source["name"]),
                "hostLogo": source.get("hostLogo", ""),
                "imageUrl": "",
                "scrapedAt": dt.datetime.now(BERLIN).isoformat(timespec="seconds"),
                "updatedAt": dt.datetime.now(BERLIN).isoformat(timespec="seconds"),
            }
        )
    return events


def normalize_events(events: list[dict], sources: list[dict]) -> list[dict]:
    source_by_id = {source["id"]: source for source in sources}
    now = dt.datetime.now(BERLIN)
    normalized = []
    seen = set()

    for event in events:
        source = source_by_id.get(event.get("sourceId"))
        if not source:
            continue
        start = parse_iso(event.get("start"))
        if not start or start <= now:
            continue
        end = parse_iso(event.get("end")) or (start + dt.timedelta(hours=1))
        event_id = event.get("id") or stable_id(
            source["id"],
            event.get("eventUrl") or source["url"],
            event.get("title", ""),
            start.isoformat(),
            event.get("location", ""),
        )
        if event_id in seen:
            continue
        seen.add(event_id)

        normalized.append(
            {
                "id": event_id,
                "title": event.get("title", "Untitled event").strip(),
                "speaker": event.get("speaker", "").strip(),
                "abstract": event.get("abstract", "").strip(),
                "sourceId": source["id"],
                "sourceName": source["name"],
                "sourceUrl": source["url"],
                "eventUrl": event.get("eventUrl") or source["url"],
                "start": start.isoformat(),
                "end": end.isoformat(),
                "timezone": event.get("timezone") or TIMEZONE,
                "location": event.get("location", "").strip(),
                "registrationRequired": bool(event.get("registrationRequired", False)),
                "registrationUrl": event.get("registrationUrl", ""),
                "topics": event.get("topics") or infer_topics(event.get("title", ""), event.get("abstract", ""), source["name"]),
                "hostLogo": event.get("hostLogo") or source.get("hostLogo", ""),
                "imageUrl": event.get("imageUrl", ""),
                "scrapedAt": event.get("scrapedAt") or dt.datetime.now(BERLIN).isoformat(timespec="seconds"),
                "updatedAt": dt.datetime.now(BERLIN).isoformat(timespec="seconds"),
            }
        )

    return sorted(normalized, key=lambda event: event["start"])


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "TuebingenTalksBot/0.1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def text_of(item: ET.Element, tag: str) -> str:
    found = item.find(tag)
    return found.text.strip() if found is not None and found.text else ""


def strip_html(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", value or "", flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def parse_date(value: str) -> dt.datetime | None:
    if not value:
        return None
    parsed = email.utils.parsedate_to_datetime(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BERLIN)
    return parsed.astimezone(BERLIN)


def parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BERLIN)
    return parsed.astimezone(BERLIN)


def infer_start(description: str) -> dt.datetime | None:
    match = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4}).{0,40}?(\d{1,2}):(\d{2})", description)
    if not match:
        return None
    day, month, year, hour, minute = map(int, match.groups())
    return dt.datetime(year, month, day, hour, minute, tzinfo=BERLIN)


def infer_location(description: str) -> str:
    match = re.search(r"(?:Ort|Location|Where):?\s*([^.;]+)", description, flags=re.I)
    return match.group(1).strip() if match else ""


def infer_topics(*values: str) -> list[str]:
    text = " ".join(values).lower()
    topics = []
    if any(term in text for term in ["ai", "machine learning", "llm", "robot", "data"]):
        topics.append("AI & ML")
    if any(term in text for term in ["cognitive", "brain", "psychology", "perception"]):
        topics.append("Cognitive Science")
    if any(term in text for term in ["geo", "climate", "environment", "water", "earth"]):
        topics.append("Earth & Environment")
    if any(term in text for term in ["startup", "founder", "transfer", "venture"]):
        topics.append("Startups & Transfer")
    if any(term in text for term in ["law", "society", "democracy", "policy"]):
        topics.append("Law & Society")
    return topics or ["Public Lecture"]


def stable_id(*parts: str) -> str:
    text = "|".join(part.strip().lower() for part in parts if part)
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
    slug = re.sub(r"[^a-z0-9]+", "-", parts[0].lower()).strip("-") if parts else "event"
    return f"{slug}-{digest}"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_if_changed(path: Path, payload: dict) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
