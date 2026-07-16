# Scraper Playbook

## Source Rules

- Treat aggregator feeds as transport, not as user-facing sources. For example, Tübingen Talks can provide events, but it should not appear as a selectable source label.
- Treat the main university calendar as transport, not as a user-facing source. For each calendar card, use the organizer/series line after the `|` marker as `sourceName`; examples include `Studium Generale`, `College of Fellows`, and `Institut für Osteuropäische Geschichte und Landeskunde und Zentrum für Medienkompetenz`.
- Prefer the real host, lab, colloquium, seminar, or event series as `sourceName`.
- When a Tübingen Talks item embeds a series name such as `Colloquium Summer Term 2026 - Wichmann lab`, normalize it to a clean source label: `Colloquium Summer Term Wichmann Lab`.
- When the main university calendar carries a Studium Generale marker, normalize it to the existing `Studium Generale` source. Markers include explicit `Studium Generale` text and known Studium Generale series titles such as `1776-2026: Was ist los in den USA?`.
- Add stable source entries for recurring derived sources so users can select or deselect them in Settings.

## Abstract Rules

- The `abstract` field should contain only descriptive event content.
- Do not repeat metadata that is already represented elsewhere:
  - source or series name
  - date or time
  - speaker
  - title
  - location
- Strip structured prefixes from aggregator descriptions, such as `Date:`, `Location:`, `Speaker:`, `Title:`, and `Abstract:`.
- If the remaining abstract is only the title after metadata removal, leave it empty.

## Event Field Rules

- `title` should be the talk/event title only.
- `speaker` should contain the person or people speaking, not HTML residue, arrows, registration labels, or organizer boilerplate.
- `sourceName` should be what a user would naturally filter by.
- `sourceId` must match an entry in `data/sources.json` unless the event is intentionally hidden.

## Maintenance

- Run `python3 scripts/scrape_events.py --normalize-only` after changing normalization rules to apply them to the checked-in feed without fetching remote pages.
- Review a few affected events in `data/events.json`, especially those imported from aggregator feeds.
