# TübingenTalks

Static PWA for discovering public talks and events around the University of Tübingen.

## Local preview

```sh
python3 -m http.server 8000
```

Then open `http://127.0.0.1:8000/`.

## Data model

- `data/sources.json` lists selectable event sources.
- `data/events.json` contains normalized future events.
- Swipe decisions, source settings, and calendar preferences stay in each browser's `localStorage`.

## Updating events

The scheduled GitHub Action runs `scripts/scrape_events.py` daily. The scraper is dependency-free and currently supports RSS sources directly while preserving the checked-in feed as a fallback for HTML sources that need source-specific parsers.
