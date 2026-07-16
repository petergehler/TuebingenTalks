const STORE_KEY = "tuebingenTalksState.v1";
const CACHE_FEED_KEY = "tuebingenTalksCachedFeed.v1";
const DEFAULT_CALENDAR = "ics";
const DEFAULT_LANGUAGE = "en";
const SOURCE_COLOR_CLASSES = ["#283f55", "#3f3d47", "#23624f", "#6b5334", "#374151"];
const I18N = {
  en: {
    addToCalendar: "Add to calendar",
    all: "All",
    appLanguage: "App language",
    calendar: "Calendar",
    defaultAddAction: "Default add action",
    emptyNoSources: "No events match your sources",
    emptyNoSourcesCopy: "Enable more sources in settings to widen the feed.",
    emptySeenAll: "You have seen all matching events",
    emptySeenAllCopy: "Revisit interested events or reset swipes to return events to the deck.",
    feed: "Feed",
    feedError: "Feed error: {error}",
    feedLoaded: "Feed loaded.",
    googleCalendar: "Google Calendar",
    icsEvent: "ICS event",
    interested: "Interested",
    language: "Language",
    lastUpdated: "Last updated {date}",
    loadingEvents: "Loading events...",
    locationTba: "Location TBA",
    noAbstract: "No abstract available yet.",
    noFeed: "No feed loaded yet.",
    openSettings: "Open settings",
    revisitInterested: "Revisit interested",
    resetSwipes: "Reset swipes",
    settings: "Settings",
    skip: "Skip",
    source: "Source",
    sources: "Sources",
    usingCachedFeed: "Using cached feed",
    visibleEvents: "{count} future events match your settings",
    when: "When",
    where: "Where",
  },
  de: {
    addToCalendar: "Zum Kalender hinzufügen",
    all: "Alle",
    appLanguage: "App-Sprache",
    calendar: "Kalender",
    defaultAddAction: "Standardaktion",
    emptyNoSources: "Keine Termine passen zu deinen Quellen",
    emptyNoSourcesCopy: "Aktiviere weitere Quellen in den Einstellungen, um den Feed zu erweitern.",
    emptySeenAll: "Du hast alle passenden Termine gesehen",
    emptySeenAllCopy: "Hole gemerkte Termine zurück oder setze deine Swipes zurück, um Termine erneut zu sehen.",
    feed: "Feed",
    feedError: "Feed-Fehler: {error}",
    feedLoaded: "Feed geladen.",
    googleCalendar: "Google Kalender",
    icsEvent: "ICS-Termin",
    interested: "Interessiert",
    language: "Sprache",
    lastUpdated: "Zuletzt aktualisiert: {date}",
    loadingEvents: "Termine werden geladen...",
    locationTba: "Ort folgt",
    noAbstract: "Noch keine Zusammenfassung verfügbar.",
    noFeed: "Noch kein Feed geladen.",
    openSettings: "Einstellungen öffnen",
    revisitInterested: "Gemerkte erneut ansehen",
    resetSwipes: "Swipes zurücksetzen",
    settings: "Einstellungen",
    skip: "Überspringen",
    source: "Quelle",
    sources: "Quellen",
    usingCachedFeed: "Zwischengespeicherter Feed",
    visibleEvents: "{count} zukünftige Termine passen zu deinen Einstellungen",
    when: "Wann",
    where: "Wo",
  },
};

const state = {
  events: [],
  sources: [],
  feedMeta: null,
  filtered: [],
  activeEvent: null,
  selectedSources: new Set(),
  decisions: {},
  history: [],
  calendarDefault: DEFAULT_CALENDAR,
  language: DEFAULT_LANGUAGE,
};

const els = {
  cardMount: document.querySelector("#cardMount"),
  feedStatus: document.querySelector("#feedStatus"),
  progressFill: document.querySelector("#progressFill"),
  settingsButton: document.querySelector("#settingsButton"),
  settingsDialog: document.querySelector("#settingsDialog"),
  settingsTitle: document.querySelector("#settingsTitle"),
  skipCue: document.querySelector("#skipCue"),
  interestedCue: document.querySelector("#interestedCue"),
  languageHeading: document.querySelector("#languageHeading"),
  languageLabel: document.querySelector("#languageLabel"),
  languageDefault: document.querySelector("#languageDefault"),
  sourcesHeading: document.querySelector("#sourcesHeading"),
  settingsSources: document.querySelector("#settingsSources"),
  calendarHeading: document.querySelector("#calendarHeading"),
  calendarDefaultLabel: document.querySelector("#calendarDefaultLabel"),
  calendarDefault: document.querySelector("#calendarDefault"),
  calendarOptionIcs: document.querySelector("#calendarOptionIcs"),
  feedHeading: document.querySelector("#feedHeading"),
  feedMeta: document.querySelector("#feedMeta"),
  resetButton: document.querySelector("#resetButton"),
  revisitSettingsButton: document.querySelector("#revisitSettingsButton"),
  undoButton: document.querySelector("#undoButton"),
  allSourcesButton: document.querySelector("#allSourcesButton"),
  cardTemplate: document.querySelector("#eventCardTemplate"),
};

init();

async function init() {
  loadLocalState();
  bindStaticEvents();
  await loadFeed();
  reconcileSources();
  render();
  registerServiceWorker();
}

function bindStaticEvents() {
  els.settingsButton.addEventListener("click", () => {
    renderSettings();
    els.settingsDialog.showModal();
  });

  els.calendarDefault.addEventListener("change", () => {
    state.calendarDefault = els.calendarDefault.value;
    saveLocalState();
  });

  els.languageDefault.addEventListener("change", () => {
    state.language = validLanguage(els.languageDefault.value);
    saveLocalState();
    render();
  });

  els.resetButton.addEventListener("click", () => {
    state.decisions = {};
    state.history = [];
    saveLocalState();
    render();
  });

  els.revisitSettingsButton.addEventListener("click", revisitInterested);

  els.undoButton.addEventListener("click", undoLastDecision);

  els.allSourcesButton.addEventListener("click", () => {
    state.selectedSources = new Set(state.sources.map((source) => source.id));
    saveLocalState();
    render();
  });
}

async function loadFeed() {
  try {
    const [eventsResponse, sourcesResponse] = await Promise.all([
      fetch("data/events.json", { cache: "no-store" }),
      fetch("data/sources.json", { cache: "no-store" }),
    ]);

    if (!eventsResponse.ok || !sourcesResponse.ok) {
      throw new Error("Feed response was not OK.");
    }

    const eventsFeed = await eventsResponse.json();
    const sourcesFeed = await sourcesResponse.json();
    const normalizedFeed = normalizeFeed(eventsFeed, sourcesFeed);
    applyFeed(normalizedFeed);
    localStorage.setItem(CACHE_FEED_KEY, JSON.stringify(normalizedFeed));
  } catch (error) {
    const cached = localStorage.getItem(CACHE_FEED_KEY);
    if (cached) {
      applyFeed(JSON.parse(cached));
      state.feedMeta = {
        ...state.feedMeta,
        offline: true,
        error: error.message,
      };
    } else {
      state.feedMeta = { offline: true, error: error.message };
      state.events = [];
      state.sources = [];
    }
  }
}

function normalizeFeed(eventsFeed, sourcesFeed) {
  const events = Array.isArray(eventsFeed.events) ? eventsFeed.events : [];
  const sources = Array.isArray(sourcesFeed.sources) ? sourcesFeed.sources : [];
  return {
    events,
    sources,
    generatedAt: eventsFeed.generatedAt || null,
    version: eventsFeed.version || null,
  };
}

function applyFeed(feed) {
  state.events = feed.events
    .filter(isFutureEvent)
    .sort((a, b) => new Date(a.start) - new Date(b.start));
  state.sources = feed.sources.filter((source) => source.selectable !== false);
  state.feedMeta = {
    generatedAt: feed.generatedAt,
    version: feed.version,
  };
}

function reconcileSources() {
  if (state.selectedSources.size === 0) {
    state.selectedSources = new Set(state.sources.map((source) => source.id));
    saveLocalState();
    return;
  }

  const validSourceIds = new Set(state.sources.map((source) => source.id));
  state.selectedSources = new Set([...state.selectedSources].filter((sourceId) => validSourceIds.has(sourceId)));
}

function loadLocalState() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY) || "{}");
    state.selectedSources = new Set(Array.isArray(saved.selectedSources) ? saved.selectedSources : []);
    state.decisions = saved.decisions || {};
    state.history = Array.isArray(saved.history) ? saved.history : [];
    state.calendarDefault = saved.calendarDefault || DEFAULT_CALENDAR;
    state.language = validLanguage(saved.language || DEFAULT_LANGUAGE);
  } catch {
    state.selectedSources = new Set();
    state.decisions = {};
    state.history = [];
    state.calendarDefault = DEFAULT_CALENDAR;
    state.language = DEFAULT_LANGUAGE;
  }
}

function saveLocalState() {
  localStorage.setItem(
    STORE_KEY,
    JSON.stringify({
      selectedSources: [...state.selectedSources],
      decisions: state.decisions,
      history: state.history.slice(-40),
      calendarDefault: state.calendarDefault,
      language: state.language,
      lastFeedVersionSeen: state.feedMeta?.version || state.feedMeta?.generatedAt || null,
    })
  );
}

function render() {
  state.filtered = getVisibleEvents();
  state.activeEvent = state.filtered[0] || null;
  renderStaticText();
  renderCard();
  renderFeedState();
  renderSettings();
}

function renderStaticText() {
  document.documentElement.lang = state.language;
  document.title = "TübingenTalks";
  els.settingsTitle.textContent = t("settings");
  els.settingsButton.setAttribute("aria-label", t("openSettings"));
  els.settingsButton.title = t("settings");
  els.undoButton.setAttribute("aria-label", state.language === "de" ? "Letzten Swipe rückgängig machen" : "Undo last swipe");
  els.undoButton.title = state.language === "de" ? "Rückgängig" : "Undo";
  els.skipCue.textContent = t("skip");
  els.interestedCue.textContent = t("interested");
  els.languageHeading.textContent = t("language");
  els.languageLabel.textContent = t("appLanguage");
  els.sourcesHeading.textContent = t("sources");
  els.allSourcesButton.textContent = t("all");
  els.calendarHeading.textContent = t("calendar");
  els.calendarDefaultLabel.textContent = t("defaultAddAction");
  els.calendarOptionIcs.textContent = t("icsEvent");
  els.calendarDefault.querySelector('[value="google"]').textContent = t("googleCalendar");
  els.feedHeading.textContent = t("feed");
  els.revisitSettingsButton.textContent = t("revisitInterested");
  els.resetButton.textContent = t("resetSwipes");
}

function getVisibleEvents() {
  return state.events.filter((event) => {
    const sourceSelected = state.selectedSources.has(event.sourceId);
    const unswiped = !state.decisions[event.id];
    return sourceSelected && unswiped && isFutureEvent(event);
  });
}

function isFutureEvent(event) {
  return event?.start && new Date(event.start).getTime() > Date.now();
}

function renderCard() {
  els.cardMount.replaceChildren();

  if (!state.activeEvent) {
    els.cardMount.append(renderEmptyCard());
    return;
  }

  const fragment = els.cardTemplate.content.cloneNode(true);
  const card = fragment.querySelector(".swipe-card");
  const heroBand = fragment.querySelector(".hero-band");
  const eventImage = fragment.querySelector(".event-image");
  const logo = fragment.querySelector(".host-logo-ghost");

  fragment.querySelector(".hero-title").textContent = state.activeEvent.title;
  fragment.querySelector(".hero-speaker").textContent = formatSpeaker(state.activeEvent);
  fragment.querySelector(".abstract").textContent = state.activeEvent.abstract || t("noAbstract");
  fragment.querySelector('[data-i18n="addToCalendar"]').textContent = t("addToCalendar");
  const detailLabels = fragment.querySelectorAll(".bottom-detail span");
  detailLabels[0].textContent = t("source");
  detailLabels[1].textContent = t("when");
  detailLabels[2].textContent = t("where");
  const sourceLink = fragment.querySelector('[data-field="source"]');
  sourceLink.textContent = state.activeEvent.sourceName;
  sourceLink.href = state.activeEvent.sourceUrl || state.activeEvent.eventUrl || "#";
  sourceLink.target = "_blank";
  sourceLink.rel = "noreferrer";
  fragment.querySelector('[data-field="when"]').textContent = formatWhen(state.activeEvent);
  fragment.querySelector('[data-field="where"]').textContent = state.activeEvent.location || t("locationTba");

  heroBand.style.background = colorForSource(state.activeEvent.sourceId);

  if (state.activeEvent.imageUrl) {
    eventImage.src = state.activeEvent.imageUrl;
    eventImage.hidden = false;
  } else {
    eventImage.hidden = true;
  }

  const logoSrc = state.activeEvent.hostLogo || sourceLogo(state.activeEvent.sourceId) || "assets/icon.svg";
  if (usesBrandedEventImage(state.activeEvent)) {
    logo.hidden = true;
  } else {
    logo.hidden = false;
    logo.src = logoSrc;
    logo.classList.toggle("is-light-logo", isLightLogo(logoSrc));
    logo.addEventListener("error", () => {
      logo.src = "assets/icon.svg";
      logo.classList.remove("is-light-logo");
    }, { once: true });
  }

  fragment.querySelector('[data-action="calendar-default"]').addEventListener("click", () => {
    addToCalendar(state.activeEvent, state.calendarDefault);
  });

  attachSwipeHandlers(card, state.activeEvent);
  els.cardMount.append(fragment);
}

function renderEmptyCard() {
  const card = document.createElement("div");
  card.className = "empty-card";
  const hasEvents = state.events.some((event) => state.selectedSources.has(event.sourceId));
  const title = document.createElement("h2");
  title.textContent = hasEvents ? t("emptySeenAll") : t("emptyNoSources");
  const copy = document.createElement("p");
  copy.textContent = hasEvents ? t("emptySeenAllCopy") : t("emptyNoSourcesCopy");
  const button = document.createElement("button");
  button.className = "secondary-button";
  button.type = "button";
  button.textContent = hasEvents ? t("revisitInterested") : t("openSettings");
  button.addEventListener("click", () => {
    if (hasEvents) {
      revisitInterested();
    } else {
      renderSettings();
      els.settingsDialog.showModal();
    }
  });
  card.append(title, copy, button);
  return card;
}

function renderSettings() {
  els.languageDefault.value = state.language;
  els.calendarDefault.value = state.calendarDefault;
  els.settingsSources.replaceChildren(
    ...state.sources.map((source) => {
      const selected = state.selectedSources.has(source.id);
      const option = document.createElement("button");
      option.className = `source-option${selected ? " is-selected" : ""}`;
      option.type = "button";
      option.setAttribute("aria-label", source.name);
      option.setAttribute("aria-pressed", String(selected));

      const logo = document.createElement("img");
      logo.className = "source-option-logo";
      logo.src = source.hostLogo || "assets/icon.svg";
      logo.alt = "";
      logo.loading = "lazy";
      logo.addEventListener("error", () => {
        logo.src = "assets/icon.svg";
      });

      const text = document.createElement("span");
      text.className = "source-option-copy";
      const name = document.createElement("span");
      name.className = "source-option-name";
      name.textContent = source.name;
      text.append(name);

      const check = document.createElement("span");
      check.className = "source-option-check";
      check.innerHTML = `<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="m5 12 4 4 10-10"></path></svg>`;

      option.addEventListener("click", () => toggleSource(source.id));
      option.append(logo, text, check);
      return option;
    })
  );

  els.revisitSettingsButton.disabled = !hasInterestedEvents();
  els.feedMeta.textContent = formatFeedMeta();
}

function hasInterestedEvents() {
  return state.events.some((event) => state.decisions[event.id] === "interested");
}

function revisitInterested() {
  let changed = false;
  for (const [eventId, decision] of Object.entries(state.decisions)) {
    if (decision === "interested") {
      delete state.decisions[eventId];
      changed = true;
    }
  }

  if (!changed) {
    return;
  }

  state.history = state.history.filter((entry) => state.decisions[entry.id]);
  saveLocalState();
  render();
  els.settingsDialog.close();
}

function renderFeedState() {
  const totalMatching = state.events.filter((event) => state.selectedSources.has(event.sourceId)).length;
  const remaining = state.filtered.length;
  const decided = totalMatching - remaining;
  els.feedStatus.textContent = t("visibleEvents", { count: remaining });
  els.progressFill.style.width = totalMatching > 0 ? `${Math.round((decided / totalMatching) * 100)}%` : "0%";
  els.undoButton.disabled = state.history.length === 0;
}

function toggleSource(sourceId) {
  if (state.selectedSources.has(sourceId)) {
    state.selectedSources.delete(sourceId);
  } else {
    state.selectedSources.add(sourceId);
  }
  saveLocalState();
  render();
}

function attachSwipeHandlers(card, event) {
  let startX = 0;
  let startY = 0;
  let currentX = 0;
  let dragging = false;

  card.addEventListener("pointerdown", (pointerEvent) => {
    if (pointerEvent.target.closest("a, button")) {
      return;
    }

    dragging = true;
    startX = pointerEvent.clientX;
    startY = pointerEvent.clientY;
    card.classList.add("dragging");
    card.setPointerCapture(pointerEvent.pointerId);
  });

  card.addEventListener("pointermove", (pointerEvent) => {
    if (!dragging) {
      return;
    }

    currentX = pointerEvent.clientX - startX;
    const currentY = pointerEvent.clientY - startY;
    const rotation = currentX / 18;
    card.style.transform = `translate3d(${currentX}px, ${currentY * 0.25}px, 0) rotate(${rotation}deg)`;
  });

  card.addEventListener("pointerup", () => finishDrag(card, event, currentX));
  card.addEventListener("pointercancel", () => finishDrag(card, event, 0));

  function finishDrag(activeCard, activeEvent, deltaX) {
    if (!dragging) {
      return;
    }
    dragging = false;
    activeCard.classList.remove("dragging");

    if (Math.abs(deltaX) > 96) {
      const decision = deltaX > 0 ? "interested" : "not_interested";
      activeCard.classList.add(deltaX > 0 ? "fly-right" : "fly-left");
      window.setTimeout(() => recordDecision(activeEvent.id, decision), 150);
      return;
    }

    currentX = 0;
    activeCard.style.transform = "";
  }
}

function recordDecision(eventId, decision) {
  state.decisions[eventId] = decision;
  state.history.push({ id: eventId, decision });
  saveLocalState();
  render();
}

function undoLastDecision() {
  const last = state.history.pop();
  if (!last) {
    return;
  }
  delete state.decisions[last.id];
  saveLocalState();
  render();
}

function addToCalendar(event, mode) {
  if (mode === "google") {
    window.open(googleCalendarUrl(event), "_blank", "noopener");
    return;
  }

  if (mode === "outlook") {
    window.open(outlookCalendarUrl(event), "_blank", "noopener");
    return;
  }

  downloadIcs(event);
}

function downloadIcs(event) {
  const ics = buildIcs(event);
  const blob = new Blob([ics], { type: "text/calendar;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${slugify(event.title)}.ics`;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function buildIcs(event) {
  const details = [event.abstract, event.eventUrl || event.sourceUrl].filter(Boolean).join("\\n\\n");
  return [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//TuebingenTalks//EN",
    "CALSCALE:GREGORIAN",
    "BEGIN:VEVENT",
    `UID:${escapeIcs(event.id)}@tuebingentalks`,
    `DTSTAMP:${formatIcsDate(new Date())}`,
    `DTSTART:${formatIcsDate(new Date(event.start))}`,
    `DTEND:${formatIcsDate(new Date(event.end || event.start))}`,
    `SUMMARY:${escapeIcs(event.title)}`,
    `DESCRIPTION:${escapeIcs(details)}`,
    `LOCATION:${escapeIcs(event.location || "")}`,
    `URL:${escapeIcs(event.eventUrl || event.sourceUrl || "")}`,
    "END:VEVENT",
    "END:VCALENDAR",
  ].join("\r\n");
}

function googleCalendarUrl(event) {
  const params = new URLSearchParams({
    action: "TEMPLATE",
    text: event.title,
    dates: `${formatGoogleDate(new Date(event.start))}/${formatGoogleDate(new Date(event.end || event.start))}`,
    location: event.location || "",
    details: [event.abstract, event.eventUrl || event.sourceUrl].filter(Boolean).join("\n\n"),
  });
  return `https://calendar.google.com/calendar/render?${params.toString()}`;
}

function outlookCalendarUrl(event) {
  const params = new URLSearchParams({
    rru: "addevent",
    path: "/calendar/action/compose",
    subject: event.title,
    startdt: new Date(event.start).toISOString(),
    enddt: new Date(event.end || event.start).toISOString(),
    location: event.location || "",
    body: [event.abstract, event.eventUrl || event.sourceUrl].filter(Boolean).join("\n\n"),
  });
  return `https://outlook.live.com/calendar/0/deeplink/compose?${params.toString()}`;
}

function formatSpeaker(event) {
  return event.speaker ? event.speaker : event.sourceName;
}

function formatWhen(event) {
  const start = new Date(event.start);
  const end = new Date(event.end || event.start);
  const date = new Intl.DateTimeFormat(dateLocale(), {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: event.timezone || "Europe/Berlin",
  }).format(start);
  const time = new Intl.DateTimeFormat(dateLocale(), {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: event.timezone || "Europe/Berlin",
  }).format(start);
  const endTime = new Intl.DateTimeFormat(dateLocale(), {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: event.timezone || "Europe/Berlin",
  }).format(end);
  return `${date}, ${time}-${endTime}`;
}

function formatFeedMeta() {
  if (!state.feedMeta) {
    return t("noFeed");
  }

  const pieces = [];
  if (state.feedMeta.generatedAt) {
    const date = new Intl.DateTimeFormat(dateLocale(), {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(state.feedMeta.generatedAt));
    pieces.push(t("lastUpdated", { date }));
  }
  if (state.feedMeta.offline) {
    pieces.push(t("usingCachedFeed"));
  }
  if (state.feedMeta.error && !state.events.length) {
    pieces.push(t("feedError", { error: state.feedMeta.error }));
  }
  return pieces.join(". ") || t("feedLoaded");
}

function t(key, replacements = {}) {
  const dictionary = I18N[state.language] || I18N[DEFAULT_LANGUAGE];
  const template = dictionary[key] || I18N[DEFAULT_LANGUAGE][key] || key;
  return Object.entries(replacements).reduce(
    (text, [name, value]) => text.replace(`{${name}}`, String(value)),
    template
  );
}

function validLanguage(language) {
  return Object.prototype.hasOwnProperty.call(I18N, language) ? language : DEFAULT_LANGUAGE;
}

function dateLocale() {
  return state.language === "de" ? "de-DE" : "en-GB";
}

function colorForSource(sourceId) {
  let hash = 0;
  for (const char of sourceId) {
    hash = (hash + char.charCodeAt(0)) % SOURCE_COLOR_CLASSES.length;
  }
  return SOURCE_COLOR_CLASSES[hash];
}

function sourceLogo(sourceId) {
  return state.sources.find((source) => source.id === sourceId)?.hostLogo;
}

function isLightLogo(src) {
  return /assets\/source-logos\/|vertical-white/i.test(src);
}

function usesBrandedEventImage(event) {
  return event?.sourceId === "cyber-valley" && Boolean(event.imageUrl);
}

function formatIcsDate(date) {
  return date.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
}

function formatGoogleDate(date) {
  return date.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
}

function escapeIcs(value) {
  return String(value || "")
    .replace(/\\/g, "\\\\")
    .replace(/\n/g, "\\n")
    .replace(/,/g, "\\,")
    .replace(/;/g, "\\;");
}

function slugify(value) {
  return String(value || "event")
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 64) || "event";
}

function registerServiceWorker() {
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }
}
