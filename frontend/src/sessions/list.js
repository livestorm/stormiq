// The v1 Sessions page's rows, column sorts, column filters and counts, from
// `GET /api/workspace-sessions` (one row per session the organization has
// fetched). Pure functions: they word and order what the route sends and
// never compute a session measure (those live on each session's Report).

import { formatLength } from "../report/format";

const EMPTY = "—";

function text(value) {
  return String(value ?? "").trim();
}

function unix(value) {
  const number = Number(value);
  return value != null && Number.isFinite(number) && number > 0 ? number : null;
}

function iso(value) {
  const time = new Date(text(value)).getTime();
  return Number.isFinite(time) ? time : null;
}

/** "12 Mar 2026" from unix seconds, in the viewer's time zone; "" when
 * unknown. */
export function formatDay(seconds, { timeZone } = {}) {
  const value = unix(seconds);
  if (value == null) return "";
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone }).format(
    new Date(value * 1000),
  );
}

function count(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

// What `GET /api/workspace-sessions` sends as `sessionName` when the session
// has no name of its own (`api_logic`).
const UNTITLED = "Untitled session";

function ownName(session) {
  const name = text(session.sessionName);
  return name === UNTITLED ? "" : name;
}

function titleOf(session) {
  return text(session.eventTitle) || ownName(session);
}

function addedBy(session) {
  return text(session.generatedByName) || text(session.generatedByEmail);
}

function addedSeconds(session) {
  const time = iso(session.generatedAtIso);
  return time == null ? null : time / 1000;
}

/** One table row, every cell worded: `{id, href, title, subtitle, length,
 * day, attendees, registrants, transcript, addedBy, addedDay}`. */
export function sessionRow(session, options = {}) {
  const attendees = count(session.attendeesCount);
  const registrants = count(session.registrantsCount);
  // Named as the Report names it: by its event's title, then by its own
  // name (a Livestorm session often has none: the route then sends a
  // placeholder, never shown as a name).
  const name = ownName(session);
  const title = text(session.eventTitle) || name || UNTITLED;
  return {
    id: text(session.sessionId),
    href: `/sessions/${encodeURIComponent(text(session.sessionId))}`,
    title,
    // The session's own name only when it says something the title doesn't.
    subtitle: name && name !== title ? name : "",
    length: formatLength(session.durationSeconds),
    day: formatDay(session.startedAtUnix, options) || EMPTY,
    attendees: attendees == null ? EMPTY : String(attendees),
    registrants: registrants == null ? "" : `of ${registrants}`,
    transcript: session.hasTranscript ? "ready" : "none",
    addedBy: addedBy(session),
    addedDay: formatDay(addedSeconds(session), options),
  };
}

/** Every distinct event, the busiest first: `[{value, title, count, label}]`. */
export function eventOptions(sessions = []) {
  const groups = new Map();
  for (const session of sessions) {
    const id = text(session.eventId);
    if (!id) continue;
    const group = groups.get(id) || { value: id, title: text(session.eventTitle) || "Untitled event", count: 0 };
    group.count += 1;
    groups.set(id, group);
  }
  return [...groups.values()]
    .sort((a, b) => b.count - a.count || a.title.localeCompare(b.title))
    .map((group) => ({ ...group, label: `${group.title} (${group.count})` }));
}

/** The events whose title holds `query` (any case); every event without one. */
export function searchEvents(options = [], query = "") {
  const needle = text(query).toLowerCase();
  return needle ? options.filter((option) => option.title.toLowerCase().includes(needle)) : options;
}

/** Who added sessions, the busiest first: `[{value, label}]`. */
export function teammateOptions(sessions = []) {
  const totals = new Map();
  for (const session of sessions) {
    const name = addedBy(session);
    if (name) totals.set(name, (totals.get(name) || 0) + 1);
  }
  return [...totals.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([name, total]) => ({ value: name, label: `${name} (${total})` }));
}

/** The counts above the table, over every session the organization has
 * (not the filtered ones): sessions, with a transcript, events. */
export function sessionCounts(sessions = []) {
  const events = new Set(sessions.map((session) => text(session.eventId)).filter(Boolean));
  return {
    sessions: sessions.length,
    transcripts: sessions.filter((session) => session.hasTranscript).length,
    events: events.size,
  };
}

// ── Sorting by a column ──
//
// The columns a click on the title sorts, each with the direction of its
// first click (newest first for dates, most first for numbers, A to Z for
// text) and how each direction reads. Only what `GET /api/workspace-sessions`
// sends is sortable: the library's cells (shape, wrote, questions) load row
// by row as they come on screen, so the page can't order by them.
export const COLUMNS = {
  date: { label: "Date", first: "desc", desc: "Newest first", asc: "Oldest first" },
  session: { label: "Session", first: "asc", asc: "A to Z", desc: "Z to A" },
  attendees: { label: "Attendees", first: "desc", desc: "Most first", asc: "Fewest first" },
  transcript: { label: "Transcript", first: "desc", desc: "Ready first", asc: "Not yet first" },
  added: { label: "Added", first: "desc", desc: "Recently added", asc: "Added longest ago" },
};

export const DEFAULT_SORT = Object.freeze({ key: "date", dir: "desc" });

/** A sort `{key, dir}` (or its JSON, as saved) checked; the default for
 * anything else. */
export function parseSort(value) {
  let sort = value;
  if (typeof value === "string") {
    try {
      sort = JSON.parse(value);
    } catch {
      return { ...DEFAULT_SORT };
    }
  }
  if (sort && Object.hasOwn(COLUMNS, sort.key) && (sort.dir === "asc" || sort.dir === "desc")) {
    return { key: sort.key, dir: sort.dir };
  }
  return { ...DEFAULT_SORT };
}

/** The sort after a click on `key`'s title: the same column reverses,
 * another starts in its own first direction. */
export function nextSort(current, key) {
  if (!Object.hasOwn(COLUMNS, key)) return parseSort(current);
  const sort = parseSort(current);
  if (sort.key === key) return { key, dir: sort.dir === "asc" ? "desc" : "asc" };
  return { key, dir: COLUMNS[key].first };
}

/** `aria-sort` for a column's header cell. */
export function ariaSort(sort, key) {
  if (sort?.key !== key) return "none";
  return sort.dir === "asc" ? "ascending" : "descending";
}

/** "Date, newest first". */
export function sortLabel(sort) {
  const { key, dir } = parseSort(sort);
  return `${COLUMNS[key].label}, ${COLUMNS[key][dir].toLowerCase()}`;
}

// The value a column orders by: a number or a string, null when unknown
// (always last, in both directions).
const SORT_VALUE = {
  date: (session) => unix(session.startedAtUnix),
  session: (session) => titleOf(session).toLocaleLowerCase() || null,
  attendees: (session) => count(session.attendeesCount),
  transcript: (session) => (session.hasTranscript ? 1 : 0),
  added: (session) => iso(session.generatedAtIso),
};

function newestFirst(a, b) {
  return (unix(b.startedAtUnix) ?? -Infinity) - (unix(a.startedAtUnix) ?? -Infinity) || 0;
}

function comparator(sort) {
  const { key, dir } = parseSort(sort);
  const value = SORT_VALUE[key];
  const sign = dir === "asc" ? 1 : -1;
  return (a, b) => {
    const x = value(a);
    const y = value(b);
    if (x == null || y == null) {
      if (x != null) return -1;
      if (y != null) return 1;
    } else {
      const order = typeof x === "string" ? x.localeCompare(y) : x - y;
      if (order) return sign * order;
    }
    // Ties, and two unknowns: the newest session first.
    return key === "date" ? 0 : newestFirst(a, b);
  };
}

/** The sessions in the order `sort` asks for; the given list is left as it is. */
export function sortSessions(sessions = [], sort = DEFAULT_SORT) {
  return [...sessions].sort(comparator(sort));
}

// ── Filtering by a column ──

const DAY_S = 24 * 60 * 60;

export const DATE_PRESETS = [
  { value: "7d", label: "Last 7 days", days: 7 },
  { value: "30d", label: "Last 30 days", days: 30 },
  { value: "90d", label: "Last 90 days", days: 90 },
  { value: "365d", label: "Last 12 months", days: 365 },
];

export const LENGTH_PRESETS = [
  { value: "short", label: "Under 30 min", max: 30 * 60 },
  { value: "medium", label: "30 min to 1 h", min: 30 * 60, max: 60 * 60 },
  { value: "long", label: "1 h or more", min: 60 * 60 },
];

export const TRANSCRIPT_OPTIONS = [
  { value: "ready", label: "Transcript ready" },
  { value: "none", label: "No transcript yet" },
];

/** Every filter, off. `query` is the search and `event` the event picker;
 * the others belong to a column. */
export function emptyFilters() {
  return { query: "", event: "", date: "", length: "", attendeesMin: null, transcript: "", addedBy: "" };
}

/** "At least" from a field's text: a whole number above zero, or null (no
 * filter). */
export function parseMinimum(value) {
  const raw = String(value ?? "").trim();
  const number = Number(raw);
  if (!raw || !Number.isFinite(number) || number < 1) return null;
  return Math.floor(number);
}

function matchesQuery(session, query) {
  const needle = text(query).toLowerCase();
  if (!needle) return true;
  return [ownName(session), session.eventTitle, session.generatedByName, session.generatedByEmail]
    .map((value) => text(value).toLowerCase())
    .some((value) => value.includes(needle));
}

function matches(session, filters, nowS) {
  if (filters.event && text(session.eventId) !== filters.event) return false;
  if (!matchesQuery(session, filters.query)) return false;
  const date = DATE_PRESETS.find((option) => option.value === filters.date);
  if (date) {
    const started = unix(session.startedAtUnix);
    if (started == null || started < nowS - date.days * DAY_S) return false;
  }
  const length = LENGTH_PRESETS.find((option) => option.value === filters.length);
  if (length) {
    const seconds = count(session.durationSeconds);
    if (!seconds) return false;
    if (length.min != null && seconds < length.min) return false;
    if (length.max != null && seconds >= length.max) return false;
  }
  const minimum = parseMinimum(filters.attendeesMin);
  if (minimum != null && !((count(session.attendeesCount) ?? -1) >= minimum)) return false;
  if (filters.transcript === "ready" && !session.hasTranscript) return false;
  if (filters.transcript === "none" && session.hasTranscript) return false;
  if (filters.addedBy && addedBy(session) !== filters.addedBy) return false;
  return true;
}

/** The sessions to show: filtered by the search (the session's and the
 * event's titles and who added it), the event and each column's filter,
 * then sorted. `now` (ms) anchors the date presets; a session with an
 * unknown value never passes that column's filter. The given list is left
 * as it is. */
export function visibleSessions(sessions = [], { filters = {}, sort = DEFAULT_SORT, now = Date.now() } = {}) {
  const all = { ...emptyFilters(), ...filters };
  const nowS = now / 1000;
  return sortSessions(
    sessions.filter((session) => matches(session, all, nowS)),
    sort,
  );
}

/** The column filters that are on, as removable chips, in the table's
 * order: `[{key, column, label}]`. The search and the event have their own
 * controls in the toolbar, so they are not chips. */
export function activeFilters(filters = {}) {
  const all = { ...emptyFilters(), ...filters };
  const chips = [];
  const date = DATE_PRESETS.find((option) => option.value === all.date);
  if (date) chips.push({ key: "date", column: "date", label: date.label });
  const length = LENGTH_PRESETS.find((option) => option.value === all.length);
  if (length) chips.push({ key: "length", column: "session", label: length.label });
  const minimum = parseMinimum(all.attendeesMin);
  if (minimum != null) chips.push({ key: "attendeesMin", column: "attendees", label: `At least ${minimum} attendees` });
  const transcript = TRANSCRIPT_OPTIONS.find((option) => option.value === all.transcript);
  if (transcript) chips.push({ key: "transcript", column: "transcript", label: transcript.label });
  if (text(all.addedBy)) chips.push({ key: "addedBy", column: "added", label: `Added by ${text(all.addedBy)}` });
  return chips;
}

/** The filters with one of them off; the given object is left as it is. */
export function clearFilter(filters = {}, key) {
  const empty = emptyFilters();
  return Object.hasOwn(empty, key) ? { ...filters, [key]: empty[key] } : { ...filters };
}

/** Whether anything narrows the list: the search, the event or a column. */
export function isFiltered(filters = {}) {
  const all = { ...emptyFilters(), ...filters };
  return Boolean(text(all.query) || all.event || activeFilters(all).length);
}

/** "12 of 48 sessions" while anything narrows the list, else "48 sessions". */
export function shownLabel(shown, total, filtered) {
  const noun = total === 1 ? "session" : "sessions";
  return filtered ? `${shown} of ${total} ${noun}` : `${total} ${noun}`;
}
