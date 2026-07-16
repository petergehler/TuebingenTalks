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
import subprocess
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SOURCES_PATH = DATA_DIR / "sources.json"
EVENTS_PATH = DATA_DIR / "events.json"
TIMEZONE = "Europe/Berlin"
BERLIN = dt.timezone(dt.timedelta(hours=2))
UNIVERSITY_LOGO = "assets/source-logos/university-tuebingen.svg"
KNOWN_CALENDAR_SOURCE_IDS = {
    "College of Fellows": "college-of-fellows",
    "Studium Generale": "studium-generale",
    "Tübinger Forum für Wissenschaftskulturen": "tfw-events",
}


def main() -> int:
    sources_feed = read_json(SOURCES_PATH)
    existing_feed = read_json(EVENTS_PATH) if EVENTS_PATH.exists() else {"events": []}
    sources = sources_feed.get("sources", [])

    if "--normalize-only" in sys.argv:
        sources = ensure_sources_for_events(sources, existing_feed.get("events", []))
        events = normalize_events(existing_feed.get("events", []), sources)
        output = {
            "version": existing_feed.get("version") or existing_feed.get("generatedAt"),
            "generatedAt": existing_feed.get("generatedAt"),
            "events": events,
        }
        write_json_if_changed(EVENTS_PATH, output)
        sources_feed["sources"] = sources
        write_json_if_changed(SOURCES_PATH, sources_feed)
        return 0

    scraped_events = []
    scraped_source_ids = set()
    for source in sources:
        try:
            source_events = scrape_source(source)
            if source_events is not None:
                scraped_source_ids.add(source["id"])
                scraped_events.extend(source_events)
        except Exception as exc:
            print(f"warning: failed to scrape {source.get('id')}: {exc}", file=sys.stderr)

    scraped_event_urls = {event.get("eventUrl") for event in scraped_events if event.get("eventUrl")}
    existing_events = [
        event
        for event in existing_feed.get("events", [])
        if event.get("sourceId") not in scraped_source_ids and event.get("eventUrl") not in scraped_event_urls
    ]
    events = existing_events + scraped_events
    sources = ensure_sources_for_events(sources, events)
    events = normalize_events(events, sources)

    generated_at = dt.datetime.now(BERLIN).isoformat(timespec="seconds")
    output = {
        "version": generated_at,
        "generatedAt": generated_at,
        "events": events,
    }

    write_json_if_changed(EVENTS_PATH, output)

    sources_feed["generatedAt"] = generated_at
    sources_feed["sources"] = sources
    write_json_if_changed(SOURCES_PATH, sources_feed)
    return 0


def scrape_source(source: dict) -> list[dict] | None:
    if source["id"] == "main-university-calendar":
        return scrape_university_calendar(source)
    if source["id"] == "mpi-is":
        return scrape_mpi_is(source)
    if source["id"] == "tuebingen-talks":
        return scrape_tuebingen_talks(source)
    if source.get("rssUrl"):
        return scrape_rss(source)
    return None


def scrape_tuebingen_talks(source: dict) -> list[dict]:
    raw = fetch_text("https://talks.tuebingen.ai/api/talks")
    payload = json.loads(raw)
    events = []
    for talk in payload.get("talks", []):
        if talk.get("disabled"):
            continue
        start = parse_tuebingen_talks_time(talk.get("timestamp"))
        if not start:
            continue
        title = strip_html(talk.get("title", ""))
        description = strip_html(talk.get("description", ""))
        description = resolve_reference_description(description, title)
        location = strip_html(talk.get("location", ""))
        speaker = strip_html(talk.get("speaker_name", ""))
        raw_description = description
        source_id, source_name, description = normalize_tuebingen_talk_metadata(
            title=title,
            description=description,
            speaker=speaker,
            fallback_source=source,
        )
        end = infer_end_from_text(raw_description, start) or (start + dt.timedelta(hours=1))
        event_url = urllib.parse.urljoin(source["url"], f"/talks/talk/id={talk.get('id')}")
        events.append(
            {
                "id": stable_id(source_id, str(talk.get("id", "")), title, start.isoformat(), location),
                "title": title or "Untitled talk",
                "speaker": speaker,
                "abstract": description,
                "sourceId": source_id,
                "sourceName": source_name,
                "sourceUrl": source["url"],
                "eventUrl": event_url,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "timezone": TIMEZONE,
                "location": location,
                "registrationRequired": False,
                "registrationUrl": "",
                "topics": infer_topics(title, description, speaker),
                "hostLogo": source.get("hostLogo", ""),
                "imageUrl": "",
                "scrapedAt": dt.datetime.now(BERLIN).isoformat(timespec="seconds"),
            }
        )
    return events


def scrape_university_calendar(source: dict) -> list[dict]:
    events = []
    seen_urls = set()
    page_urls = [source["url"]] + [urllib.parse.urljoin(source["url"], f"seite/{page}/") for page in range(2, 8)]
    for page_url in page_urls:
        raw = fetch_text(page_url)
        page_events = parse_university_calendar_page(raw, source)
        new_page_events = [event for event in page_events if event["eventUrl"] not in seen_urls]
        for event in new_page_events:
            seen_urls.add(event["eventUrl"])
        events.extend(new_page_events)
        if not page_events:
            break
    return events


def parse_university_calendar_page(raw: str, source: dict) -> list[dict]:
    blocks = re.findall(
        r'<div class="ut-news-item"[^>]*>(.*?)(?=<div class="ut-news-item"|\s*</div>\s*</div>\s*<div class="page-navigation"|</div>\s*</div>\s*</main>)',
        raw,
        flags=re.S,
    )
    events = []
    for block in blocks:
        link_match = re.search(r'<a[^>]+class="[^"]*ut-news-item__title-link[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, flags=re.S)
        if not link_match:
            continue
        event_url = urllib.parse.urljoin("https://uni-tuebingen.de/", html.unescape(link_match.group(1)))
        title = strip_html(link_match.group(2))
        lines = html_lines(block)
        start, end = parse_calendar_dates(lines)
        if not start:
            continue
        location = value_after_label(lines, "Veranstaltungsort:")
        speaker = value_after_label(lines, "Referent/in:")
        abstract = calendar_abstract(lines, title)
        organizer = calendar_organizer(lines)
        source_id, source_name = calendar_source_identity(organizer, source)
        events.append(
            {
                "id": stable_id(source_id, event_url, title, start.isoformat(), location),
                "title": title or "Untitled event",
                "speaker": speaker,
                "abstract": abstract,
                "sourceId": source_id,
                "sourceName": source_name,
                "sourceUrl": source["url"],
                "eventUrl": event_url,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "timezone": TIMEZONE,
                "location": location,
                "registrationRequired": "anmeldung" in block.lower() or "registration" in block.lower(),
                "registrationUrl": event_url,
                "topics": infer_topics(title, abstract, speaker, organizer),
                "hostLogo": "",
                "imageUrl": "",
                "scrapedAt": dt.datetime.now(BERLIN).isoformat(timespec="seconds"),
            }
        )
    return events


def scrape_mpi_is(source: dict) -> list[dict]:
    raw = fetch_text(source["url"])
    rows = re.findall(r'<div class="row mb-5">(.*?)(?=<div class="row mb-5">|</div>\s*</div>\s*</div>\s*</div>\s*</main>)', raw, flags=re.S)
    events = []
    for row in rows:
        lines = html_lines(row)
        date_line = next((line for line in lines if re.search(r"\d{2}-\d{2}-\d{4}", line)), "")
        start, end = parse_european_date_range(date_line)
        if not start:
            continue
        title_match = re.search(r"<h2[^>]*>.*?<a[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>", row, flags=re.S)
        if title_match:
            event_url = urllib.parse.urljoin("https://is.mpg.de/", html.unescape(title_match.group(1)))
            title = strip_html(title_match.group(2))
        else:
            event_url = source["url"]
            title = lines[5] if len(lines) > 5 else "Untitled event"
        speaker_match = re.search(r"<h3[^>]*>.*?<small[^>]*>(.*?)</small>", row, flags=re.S)
        speaker = strip_html(speaker_match.group(1)) if speaker_match else ""
        event_type = lines[3] if len(lines) > 3 else "Event"
        component = lines[4] if len(lines) > 4 else ""
        abstract = mpi_abstract(lines, title)
        events.append(
            {
                "id": stable_id(source["id"], event_url, title, start.isoformat(), ""),
                "title": title,
                "speaker": speaker or component,
                "abstract": abstract,
                "sourceId": source["id"],
                "sourceName": source["name"],
                "sourceUrl": source["url"],
                "eventUrl": event_url,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "timezone": TIMEZONE,
                "location": "MPI-IS Tübingen",
                "registrationRequired": "registration" in " ".join(lines).lower(),
                "registrationUrl": event_url,
                "topics": infer_topics(title, abstract, speaker, component, event_type),
                "hostLogo": source.get("hostLogo", ""),
                "imageUrl": "",
                "scrapedAt": dt.datetime.now(BERLIN).isoformat(timespec="seconds"),
            }
        )
    return events


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
    direct_source_by_title = {
        normalize_text(event.get("title", "")): (event.get("sourceId"), event.get("sourceName", ""))
        for event in events
        if event.get("sourceId") != "tuebingen-talks" and event.get("title")
    }
    now = dt.datetime.now(BERLIN)
    normalized = []
    seen = set()

    for event in events:
        if event.get("sourceId") == "tuebingen-talks":
            source_id, source_name, abstract = normalize_tuebingen_talk_metadata(
                title=event.get("title", ""),
                description=event.get("abstract", ""),
                speaker=event.get("speaker", ""),
                fallback_source=source_by_id.get("tuebingen-talks", {"id": "tuebingen-talks", "name": "Tübingen Talks"}),
            )
            event = {
                **event,
                "sourceId": source_id,
                "sourceName": source_name,
                "abstract": abstract,
                "hostLogo": "" if source_id != "tuebingen-talks" else event.get("hostLogo", ""),
            }
            if source_id == "imported-talks":
                direct_source_id, direct_source_name = direct_source_by_title.get(normalize_text(event.get("title", "")), ("", ""))
                if direct_source_id:
                    event = {
                        **event,
                        "sourceId": direct_source_id,
                        "sourceName": direct_source_name or source_by_id.get(direct_source_id, {}).get("name", ""),
                        "hostLogo": "",
                    }

        if is_studium_generale_event(event):
            event = {
                **event,
                "sourceId": "studium-generale",
                "sourceName": "Studium Generale",
                "sourceUrl": "https://uni-tuebingen.de/universitaet/im-dialog/studium-generale/",
                "hostLogo": "",
            }

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

        title = event.get("title", "Untitled event").strip()
        speaker = clean_person_field(event.get("speaker", ""))
        abstract = clean_event_abstract(
            event.get("abstract", ""),
            title=title,
            speaker=speaker,
            source_name=event.get("sourceName") or source["name"],
            start=start,
        )
        host_logo = event.get("hostLogo") or source.get("hostLogo", "")
        if host_logo == "assets/icon.svg" and source.get("hostLogo") not in {"", host_logo}:
            host_logo = source.get("hostLogo", "")

        normalized.append(
            {
                "id": event_id,
                "title": title,
                "speaker": speaker,
                "abstract": abstract,
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
                "hostLogo": host_logo,
                "imageUrl": event.get("imageUrl", ""),
                "scrapedAt": event.get("scrapedAt") or dt.datetime.now(BERLIN).isoformat(timespec="seconds"),
                "updatedAt": dt.datetime.now(BERLIN).isoformat(timespec="seconds"),
            }
        )

    return sorted(normalized, key=lambda event: event["start"])


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "TuebingenTalksBot/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError:
        result = subprocess.run(
            ["curl", "-L", "-A", "TuebingenTalksBot/0.1", url],
            check=True,
            capture_output=True,
        )
        return result.stdout.decode("utf-8", errors="replace")


def resolve_reference_description(description: str, title: str = "") -> str:
    url = reference_only_url(description)
    if not url:
        return description

    try:
        linked_page = fetch_text(url)
    except Exception as exc:
        print(f"warning: failed to resolve description link {url}: {exc}", file=sys.stderr)
        return description

    resolved = extract_linked_page_description(linked_page, title)
    return resolved or description


def reference_only_url(value: str) -> str:
    match = re.fullmatch(
        r"(?is)\s*(?:(?:please\s+)?(?:see|siehe|details?|more\s+info(?:rmation)?)(?:\s+at)?\s*:?\s*)?(https?://\S+)\s*",
        value or "",
    )
    if not match:
        return ""
    return match.group(1).rstrip(".,;)")


def extract_linked_page_description(raw: str, title: str = "") -> str:
    for row in re.findall(r"<tr\b[^>]*>.*?</tr>", raw or "", flags=re.I | re.S):
        row_text = " ".join(html_lines(row))
        if title and normalize_text(title) not in normalize_text(row_text):
            continue
        heading = first_heading(raw)
        text = f"{heading}: {row_text}" if heading and heading not in row_text else row_text
        return truncate_text(text)

    main_match = re.search(r"<main\b[^>]*>(.*?)</main>", raw or "", flags=re.I | re.S)
    content = main_match.group(1) if main_match else raw
    content = re.sub(r"<(?:script|style|nav|form)\b[^>]*>.*?</(?:script|style|nav|form)>", " ", content, flags=re.I | re.S)
    lines = html_lines(content)
    if title:
        normalized_title = normalize_text(title)
        for index, line in enumerate(lines):
            if normalized_title in normalize_text(line):
                return truncate_text(" ".join(lines[max(0, index - 3):index + 8]))
    return truncate_text(" ".join(lines[:10]))


def first_heading(raw: str) -> str:
    meta_match = re.search(r'<meta\b[^>]*name=["\']title["\'][^>]*content=["\']([^"\']+)["\']', raw or "", flags=re.I | re.S)
    if meta_match:
        return strip_html(meta_match.group(1)).split("|")[0].strip()
    for tag in ("h1", "h2"):
        match = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", raw or "", flags=re.I | re.S)
        if match:
            return strip_html(match.group(1))
    title_match = re.search(r"<title\b[^>]*>(.*?)</title>", raw or "", flags=re.I | re.S)
    return strip_html(title_match.group(1)).split("|")[0].strip() if title_match else ""


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).casefold().strip()


def normalize_tuebingen_talk_metadata(
    *,
    title: str,
    description: str,
    speaker: str,
    fallback_source: dict,
) -> tuple[str, str, str]:
    source_id = fallback_source["id"]
    source_name = fallback_source["name"]
    cleaned = description or ""

    if source_id == "tuebingen-talks":
        source_id = "imported-talks"
        source_name = "Imported talks"

    series_match = re.search(
        r"\b(?P<series>Colloquium\s+Summer\s+Term(?:\s+\d{4})?\s*[-–]\s*Wichmann\s+lab)\s*:\s*",
        cleaned,
        flags=re.I,
    )
    if series_match:
        source_id = "wichmann-lab-colloquium"
        source_name = "Colloquium Summer Term Wichmann Lab"
        cleaned = cleaned[series_match.end():]

    if re.search(r"\bCognitive Science Colloquium\b", cleaned, flags=re.I):
        source_id = "cognitive-science"
        source_name = "Cognitive Science"

    cleaned = extract_structured_abstract(cleaned) or cleaned
    cleaned = clean_event_abstract(cleaned, title=title, speaker=speaker, source_name=source_name)
    return source_id, source_name, cleaned


def is_studium_generale_event(event: dict) -> bool:
    text = normalize_text(
        " ".join(
            str(event.get(key, ""))
            for key in ("title", "speaker", "abstract", "eventUrl", "sourceUrl")
        )
    )
    if not text:
        return False
    return any(
        marker in text
        for marker in (
            "studium generale",
            "1776–2026: was ist los in den usa",
            "1776-2026: was ist los in den usa",
        )
    )


def extract_structured_abstract(value: str) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    abstract_match = re.search(r"\bAbstract\s*:\s*(.+)$", text, flags=re.I | re.S)
    if abstract_match:
        return abstract_match.group(1).strip()

    text = re.sub(r"^\s*Date\s*:\s*.*?(?=\s+(?:Location|Speaker|Title|Abstract)\s*:|$)", "", text, flags=re.I)
    text = re.sub(r"^\s*Location\s*:\s*.*?(?=\s+(?:Speaker|Title|Abstract)\s*:|$)", "", text, flags=re.I)
    text = re.sub(r"^\s*Speaker\s*:\s*.*?(?=\s+(?:Title|Abstract)\s*:|$)", "", text, flags=re.I)
    text = re.sub(r"^\s*Title\s*:\s*", "", text, flags=re.I)
    return text.strip()


def clean_event_abstract(
    value: str,
    *,
    title: str = "",
    speaker: str = "",
    source_name: str = "",
    start: dt.datetime | None = None,
) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    if not text:
        return ""

    if title:
        title_index = text.casefold().find(title.casefold())
        if 0 <= title_index < 220:
            text = text[title_index + len(title):].strip()

    for removable in event_metadata_phrases(title=title, speaker=speaker, source_name=source_name, start=start):
        if not removable:
            continue
        text = remove_metadata_phrase(text, removable)

    text = re.sub(r"^\s*[-–:|,]+\s*", "", text)
    return "" if title and normalize_text(text) == normalize_text(title) else text.strip()


def event_metadata_phrases(
    *,
    title: str = "",
    speaker: str = "",
    source_name: str = "",
    start: dt.datetime | None = None,
) -> list[str]:
    phrases = [source_name, title, speaker, short_speaker_name(speaker)]
    if start:
        phrases.extend(
            [
                start.strftime("%d.%m.%Y"),
                f"{start.day}.{start.month}.{start.year}",
                f"{start.day}.{start.month}.{start.year:04d}",
            ]
        )
    return [phrase for phrase in phrases if phrase]


def remove_metadata_phrase(text: str, phrase: str) -> str:
    escaped = re.escape(phrase.strip())
    if not escaped:
        return text
    text = re.sub(rf"^\s*{escaped}\s*[-–:|,]?\s*", "", text, flags=re.I)
    text = re.sub(rf"\s+{escaped}\s*$", "", text, flags=re.I)
    return text.strip()


def short_speaker_name(speaker: str) -> str:
    return re.split(r",|\(|/", speaker or "", maxsplit=1)[0].strip()


def clean_person_field(value: str) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    return "" if text in {"-->", "-", "—"} else text


def truncate_text(value: str, limit: int = 680) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(".,;:") + "…"


def text_of(item: ET.Element, tag: str) -> str:
    found = item.find(tag)
    return found.text.strip() if found is not None and found.text else ""


def strip_html(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", value or "", flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def html_lines(value: str) -> list[str]:
    value = re.sub(r"<br\s*/?>", "\n", value or "", flags=re.I)
    value = re.sub(r"</(?:p|h\d|div|li|td|th)>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return [re.sub(r"\s+", " ", line).strip() for line in value.splitlines() if line.strip()]


def parse_tuebingen_talks_time(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=dt.timezone.utc).astimezone(BERLIN)


def infer_end_from_text(text: str, start: dt.datetime) -> dt.datetime | None:
    text = text or ""
    date_line = re.search(r"(?:Date|Datum):[^\n]*(\d{1,2})[:.](\d{2})\s*[-–]\s*(\d{1,2})[:.](\d{2})", text, flags=re.I)
    if date_line:
        _, _, end_hour, end_minute = map(int, date_line.groups())
        end = start.replace(hour=end_hour, minute=end_minute)
        if end <= start:
            end += dt.timedelta(days=1)
        return end
    time_range = re.search(r"(\d{1,2})[:.](\d{2})\s*[-–]\s*(\d{1,2})[:.](\d{2})", text)
    if time_range:
        _, _, end_hour, end_minute = map(int, time_range.groups())
        end = start.replace(hour=end_hour, minute=end_minute)
        if end <= start:
            end += dt.timedelta(days=1)
        return end
    return None


def parse_calendar_dates(lines: list[str]) -> tuple[dt.datetime | None, dt.datetime | None]:
    try:
        index = lines.index("Datum:")
    except ValueError:
        return None, None

    detail = []
    for line in lines[index + 1:]:
        if line in {"Veranstaltungsort:", "Referent/in:", "Mehr erfahren"}:
            break
        detail.append(line)

    start_date = next((parse_german_date(line) for line in detail if parse_german_date(line)), None)
    if not start_date:
        return None, None

    times = [parse_time(line) for line in detail if parse_time(line)]
    start_time = times[0] if times else (9, 0)
    start = dt.datetime(start_date.year, start_date.month, start_date.day, start_time[0], start_time[1], tzinfo=BERLIN)

    end_date = start_date
    date_values = [parse_german_date(line) for line in detail if parse_german_date(line)]
    if len(date_values) > 1:
        end_date = date_values[-1]
    end_time = times[1] if len(times) > 1 else ((17, 0) if len(date_values) > 1 or not times else (start.hour + 1, start.minute))
    end = dt.datetime(end_date.year, end_date.month, end_date.day, end_time[0], end_time[1], tzinfo=BERLIN)
    if end <= start:
        end = start + dt.timedelta(hours=1)
    return start, end


def parse_german_date(value: str) -> dt.date | None:
    match = re.search(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b", value or "")
    if not match:
        return None
    day, month, year = map(int, match.groups())
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def parse_european_date_range(value: str) -> tuple[dt.datetime | None, dt.datetime | None]:
    dates = re.findall(r"\b(\d{2})-(\d{2})-(\d{4})\b", value or "")
    if not dates:
        return None, None
    try:
        start_date = dt.date(int(dates[0][2]), int(dates[0][1]), int(dates[0][0]))
        end_date = dt.date(int(dates[-1][2]), int(dates[-1][1]), int(dates[-1][0]))
    except ValueError:
        return None, None
    start = dt.datetime(start_date.year, start_date.month, start_date.day, 12, 0, tzinfo=BERLIN)
    end_hour = 13 if start_date == end_date else 17
    end = dt.datetime(end_date.year, end_date.month, end_date.day, end_hour, 0, tzinfo=BERLIN)
    return start, end


def parse_time(value: str) -> tuple[int, int] | None:
    match = re.search(r"\b(\d{1,2}):(\d{2})\b", value or "")
    if not match:
        return None
    hour, minute = map(int, match.groups())
    if hour > 23 or minute > 59:
        return None
    return hour, minute


def value_after_label(lines: list[str], label: str) -> str:
    try:
        index = lines.index(label)
    except ValueError:
        return ""
    return lines[index + 1] if index + 1 < len(lines) else ""


def calendar_abstract(lines: list[str], title: str) -> str:
    try:
        index = lines.index(title)
    except ValueError:
        return ""
    abstract = []
    for line in lines[index + 1:]:
        if line == "Datum:":
            break
        if line not in {"Mehr erfahren", "|"}:
            abstract.append(line)
    return " ".join(abstract).strip()


def calendar_organizer(lines: list[str]) -> str:
    if "|" in lines:
        index = lines.index("|")
        if index + 1 < len(lines):
            return clean_source_name(lines[index + 1])
    return ""


def calendar_source_identity(organizer: str, fallback_source: dict) -> tuple[str, str]:
    source_name = clean_source_name(organizer)
    if not source_name:
        return fallback_source["id"], fallback_source["name"]
    source_id = KNOWN_CALENDAR_SOURCE_IDS.get(source_name) or f"calendar-{slugify_source_id(source_name)}"
    return source_id, source_name


def ensure_sources_for_events(sources: list[dict], events: list[dict]) -> list[dict]:
    updated_sources = list(sources)
    source_by_id = {source["id"]: source for source in updated_sources}
    main_calendar = source_by_id.get("main-university-calendar", {})

    for event in events:
        source_id = event.get("sourceId", "")
        source_name = clean_source_name(event.get("sourceName", ""))
        if not source_id or not source_name or source_id in source_by_id:
            continue
        source = {
            "id": source_id,
            "name": source_name,
            "type": "html",
            "url": event.get("sourceUrl") or main_calendar.get("url") or event.get("eventUrl", ""),
            "selectable": True,
            "hostLogo": event.get("hostLogo") or UNIVERSITY_LOGO,
        }
        updated_sources.append(source)
        source_by_id[source_id] = source

    return updated_sources


def clean_source_name(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip(" |,-")


def slugify_source_id(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-")
    return (slug[:64].rstrip("-") or "source")


def mpi_abstract(lines: list[str], title: str) -> str:
    try:
        index = lines.index(title)
    except ValueError:
        return ""
    abstract_lines = []
    for line in lines[index + 1:]:
        if line in {"<br>"}:
            continue
        abstract_lines.append(line)
    return " ".join(abstract_lines).strip()


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
