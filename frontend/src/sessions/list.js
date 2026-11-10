// The v1 Sessions page's rows, filters and counts, from `GET
// /api/workspace-sessions` (one row per session the organization has
// fetched). Pure functions: they word and order what the route sends and
// never compute a session measure (those live on each session's Report).

import { formatLength } from "../report/format";

const EMPTY = "—";

export const SORTS = [
  { value: "sessionDesc", label: "Session date, newest first" },
  { value: "sessionAsc", label: "Session date, oldest first" },
  { value: "addedDesc", label: "Recently added" },
  { value: "titleAsc", label: "Title, A to Z" },
];

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

/** One table row, every cell worded: `{id, href, title, subtitle, length,
 * day, attendees, registrants, transcript, addedBy}`. */
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
    addedBy: text(session.generatedByName) || text(session.generatedByEmail),
  };
}

/** Every distinct event, the busiest first: `[{value, label}]`. */
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
    .map((group) => ({ value: group.value, label: `${group.title} (${group.count})` }));
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

function compare(mode) {
  // Sessions without a start time come last in both date orders.
  const newest = (a, b) => (unix(b.startedAtUnix) ?? -Infinity) - (unix(a.startedAtUnix) ?? -Infinity) || 0;
  const oldest = (a, b) => (unix(a.startedAtUnix) ?? Infinity) - (unix(b.startedAtUnix) ?? Infinity) || 0;
  switch (mode) {
    case "sessionAsc":
      return oldest;
    case "addedDesc":
      return (a, b) => (iso(b.generatedAtIso) ?? 0) - (iso(a.generatedAtIso) ?? 0) || newest(a, b);
    case "titleAsc":
      return (a, b) => titleOf(a).localeCompare(titleOf(b)) || newest(a, b);
    case "sessionDesc":
    default:
      return newest;
  }
}

/** The sessions to show: filtered by event and by a search over the
 * session's and the event's titles and who added it, then sorted. The
 * given list is left as it is. */
export function visibleSessions(sessions = [], { query = "", event = "", sort = "sessionDesc" } = {}) {
  const needle = text(query).toLowerCase();
  return sessions
    .filter((session) => !event || text(session.eventId) === event)
    .filter((session) => {
      if (!needle) return true;
      return [ownName(session), session.eventTitle, session.generatedByName, session.generatedByEmail]
        .map((value) => text(value).toLowerCase())
        .some((value) => value.includes(needle));
    })
    .sort(compare(sort));
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
