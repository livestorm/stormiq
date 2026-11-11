// The v1 Sessions page's rows, column sorts, column filters and counts
// (src/sessions/list.js).

import { describe, expect, it } from "vitest";
import {
  COLUMNS,
  DEFAULT_SORT,
  activeFilters,
  ariaSort,
  clearFilter,
  emptyFilters,
  eventOptions,
  formatDay,
  isFiltered,
  nextSort,
  parseMinimum,
  parseSort,
  searchEvents,
  sessionCounts,
  sessionRow,
  shownLabel,
  sortLabel,
  teammateOptions,
  visibleSessions,
} from "../src/sessions/list";

const MARCH_12 = Date.UTC(2026, 2, 12, 14, 0) / 1000;
const FEB_4 = Date.UTC(2026, 1, 4, 10, 0) / 1000;
// "Now" for the date presets: 20 March 2026.
const NOW = Date.UTC(2026, 2, 20, 12, 0);

const SESSIONS = [
  {
    sessionId: "s-1", sessionName: "Spring release tour", eventId: "e-1", eventTitle: "Release tours",
    startedAtUnix: MARCH_12, durationSeconds: 2700, attendeesCount: 251, registrantsCount: 600,
    hasTranscript: true, generatedByName: "Camille Martin", generatedAtIso: "2026-03-13T09:00:00+00:00",
  },
  {
    sessionId: "s-2", sessionName: "Partner kickoff", eventId: "e-2", eventTitle: "Partner kickoff",
    startedAtUnix: FEB_4, durationSeconds: 3600, attendeesCount: 64, registrantsCount: null,
    hasTranscript: false, generatedByEmail: "hugo@example.test", generatedAtIso: "2026-03-20T09:00:00+00:00",
  },
  {
    sessionId: "s/3", sessionName: "", eventId: "e-1", eventTitle: "Release tours",
    startedAtUnix: null, durationSeconds: null, attendeesCount: undefined, hasTranscript: false,
  },
];

const ids = (list) => list.map((s) => s.sessionId);
const show = (options) => ids(visibleSessions(SESSIONS, { now: NOW, ...options }));

describe("sessionRow", () => {
  it("names a session by its event, with its own name under it, and words every cell", () => {
    expect(sessionRow(SESSIONS[0], { timeZone: "UTC" })).toEqual({
      id: "s-1", href: "/sessions/s-1", title: "Release tours", subtitle: "Spring release tour", length: "45 min",
      day: "12 Mar 2026", attendees: "251", registrants: "of 600", transcript: "ready", addedBy: "Camille Martin",
      addedDay: "13 Mar 2026",
    });
  });

  it("doesn't repeat a name that is the event's, and falls back to the email", () => {
    expect(sessionRow(SESSIONS[1], { timeZone: "UTC" })).toMatchObject({
      title: "Partner kickoff", subtitle: "", registrants: "", transcript: "none", addedBy: "hugo@example.test",
    });
  });

  it("never shows the route's placeholder as a name, and encodes the id", () => {
    expect(sessionRow({ ...SESSIONS[2], sessionName: "Untitled session" })).toMatchObject({
      title: "Release tours", subtitle: "", day: "—", attendees: "—", length: "", href: "/sessions/s%2F3", addedDay: "",
    });
    expect(sessionRow({ sessionId: "x", sessionName: "Spring tour" }).title).toBe("Spring tour");
    expect(sessionRow({ sessionId: "x", sessionName: "Untitled session" }).title).toBe("Untitled session");
  });
});

describe("column sorts", () => {
  it("sorts by session date, newest first, by default; unknown dates last both ways", () => {
    expect(show()).toEqual(["s-1", "s-2", "s/3"]);
    expect(show({ sort: { key: "date", dir: "asc" } })).toEqual(["s-2", "s-1", "s/3"]);
  });

  it("starts each column in its own direction, and a second click reverses it", () => {
    expect(nextSort(DEFAULT_SORT, "date")).toEqual({ key: "date", dir: "asc" });
    expect(nextSort({ key: "date", dir: "asc" }, "date")).toEqual({ key: "date", dir: "desc" });
    expect(nextSort(DEFAULT_SORT, "session")).toEqual({ key: "session", dir: "asc" });
    expect(nextSort(DEFAULT_SORT, "attendees")).toEqual({ key: "attendees", dir: "desc" });
    expect(nextSort(DEFAULT_SORT, "added")).toEqual({ key: "added", dir: "desc" });
    expect(nextSort({ key: "added", dir: "desc" }, "wrote")).toEqual({ key: "added", dir: "desc" });
  });

  it("sorts by title, attendees, transcript and when it was added; unknowns last", () => {
    // By the title shown (the event's): Partner kickoff, then the two Release tours, newest first.
    expect(show({ sort: { key: "session", dir: "asc" } })).toEqual(["s-2", "s-1", "s/3"]);
    expect(show({ sort: { key: "session", dir: "desc" } })).toEqual(["s-1", "s/3", "s-2"]);
    expect(show({ sort: { key: "attendees", dir: "desc" } })).toEqual(["s-1", "s-2", "s/3"]);
    expect(show({ sort: { key: "attendees", dir: "asc" } })).toEqual(["s-2", "s-1", "s/3"]);
    expect(show({ sort: { key: "transcript", dir: "desc" } })).toEqual(["s-1", "s-2", "s/3"]);
    expect(show({ sort: { key: "transcript", dir: "asc" } })).toEqual(["s-2", "s/3", "s-1"]);
    expect(show({ sort: { key: "added", dir: "desc" } })).toEqual(["s-2", "s-1", "s/3"]);
    expect(show({ sort: { key: "added", dir: "asc" } })).toEqual(["s-1", "s-2", "s/3"]);
  });

  it("reads a saved sort back, and falls back to the default for anything else", () => {
    expect(parseSort(JSON.stringify({ key: "attendees", dir: "asc" }))).toEqual({ key: "attendees", dir: "asc" });
    expect(parseSort("{not json")).toEqual(DEFAULT_SORT);
    expect(parseSort({ key: "wrote", dir: "asc" })).toEqual(DEFAULT_SORT);
    expect(parseSort({ key: "toString", dir: "asc" })).toEqual(DEFAULT_SORT);
    expect(parseSort({ key: "date", dir: "up" })).toEqual(DEFAULT_SORT);
    expect(parseSort(null)).toEqual(DEFAULT_SORT);
  });

  it("words the sort for aria-sort and for the phone's sheet", () => {
    expect(ariaSort(DEFAULT_SORT, "date")).toBe("descending");
    expect(ariaSort({ key: "session", dir: "asc" }, "session")).toBe("ascending");
    expect(ariaSort(DEFAULT_SORT, "session")).toBe("none");
    expect(sortLabel(DEFAULT_SORT)).toBe("Date, newest first");
    expect(sortLabel({ key: "session", dir: "desc" })).toBe("Session, z to a");
    for (const column of Object.values(COLUMNS)) expect(column[column.first]).toBeTruthy();
  });

  it("never changes the list it is given", () => {
    const before = ids(SESSIONS);
    visibleSessions(SESSIONS, { sort: { key: "session", dir: "asc" } });
    expect(ids(SESSIONS)).toEqual(before);
  });
});

describe("column filters", () => {
  it("filters by event and searches titles and who added it", () => {
    expect(show({ filters: { event: "e-1" } })).toEqual(["s-1", "s/3"]);
    expect(show({ filters: { query: "  HUGO " } })).toEqual(["s-2"]);
    expect(show({ filters: { query: "release" } })).toEqual(["s-1", "s/3"]);
  });

  it("keeps the sessions of a date range; an unknown date never passes", () => {
    expect(show({ filters: { date: "7d" } })).toEqual([]);
    expect(show({ filters: { date: "30d" } })).toEqual(["s-1"]);
    expect(show({ filters: { date: "90d" } })).toEqual(["s-1", "s-2"]);
    expect(show({ filters: { date: "nonsense" } })).toEqual(["s-1", "s-2", "s/3"]);
  });

  it("keeps sessions by length: an hour is '1 h or more', not '30 min to 1 h'", () => {
    expect(show({ filters: { length: "short" } })).toEqual([]);
    expect(show({ filters: { length: "medium" } })).toEqual(["s-1"]);
    expect(show({ filters: { length: "long" } })).toEqual(["s-2"]);
  });

  it("keeps sessions with at least so many attendees; an unknown count never passes", () => {
    expect(show({ filters: { attendeesMin: "100" } })).toEqual(["s-1"]);
    expect(show({ filters: { attendeesMin: 64 } })).toEqual(["s-1", "s-2"]);
    expect(show({ filters: { attendeesMin: "" } })).toEqual(["s-1", "s-2", "s/3"]);
    expect(parseMinimum("12.7")).toBe(12);
    expect(parseMinimum("0")).toBeNull();
    expect(parseMinimum("-3")).toBeNull();
    expect(parseMinimum("abc")).toBeNull();
    expect(parseMinimum(null)).toBeNull();
  });

  it("filters by transcript and by who added it, and combines filters", () => {
    expect(show({ filters: { transcript: "ready" } })).toEqual(["s-1"]);
    expect(show({ filters: { transcript: "none" } })).toEqual(["s-2", "s/3"]);
    expect(show({ filters: { addedBy: "hugo@example.test" } })).toEqual(["s-2"]);
    expect(show({ filters: { event: "e-1", transcript: "none" } })).toEqual(["s/3"]);
  });

  it("lists the column filters that are on as chips, in the table's order", () => {
    expect(activeFilters(emptyFilters())).toEqual([]);
    const filters = { ...emptyFilters(), query: "x", event: "e-1", addedBy: "Camille Martin", date: "30d", attendeesMin: "50", length: "long", transcript: "ready" };
    expect(activeFilters(filters)).toEqual([
      { key: "date", column: "date", label: "Last 30 days" },
      { key: "length", column: "session", label: "1 h or more" },
      { key: "attendeesMin", column: "attendees", label: "At least 50 attendees" },
      { key: "transcript", column: "transcript", label: "Transcript ready" },
      { key: "addedBy", column: "added", label: "Added by Camille Martin" },
    ]);
    expect(clearFilter(filters, "date").date).toBe("");
    expect(clearFilter(filters, "attendeesMin").attendeesMin).toBeNull();
    expect(filters.date).toBe("30d");
    expect(clearFilter(filters, "nonsense")).toEqual(filters);
  });

  it("says whether anything narrows the list, and words the count", () => {
    expect(isFiltered(emptyFilters())).toBe(false);
    expect(isFiltered({ query: "  " })).toBe(false);
    expect(isFiltered({ query: "a" })).toBe(true);
    expect(isFiltered({ event: "e-1" })).toBe(true);
    expect(isFiltered({ attendeesMin: "5" })).toBe(true);
    expect(shownLabel(12, 48, true)).toBe("12 of 48 sessions");
    expect(shownLabel(48, 48, false)).toBe("48 sessions");
    expect(shownLabel(1, 1, false)).toBe("1 session");
  });
});

describe("options and counts", () => {
  it("lists events, the busiest first, and searches them by title", () => {
    const events = eventOptions(SESSIONS);
    expect(events).toEqual([
      { value: "e-1", title: "Release tours", count: 2, label: "Release tours (2)" },
      { value: "e-2", title: "Partner kickoff", count: 1, label: "Partner kickoff (1)" },
    ]);
    expect(searchEvents(events, " PARTNER ").map((e) => e.value)).toEqual(["e-2"]);
    expect(searchEvents(events, "")).toBe(events);
  });

  it("lists who added sessions, the busiest first", () => {
    expect(teammateOptions([...SESSIONS, { ...SESSIONS[1], sessionId: "s-4" }])).toEqual([
      { value: "hugo@example.test", label: "hugo@example.test (2)" },
      { value: "Camille Martin", label: "Camille Martin (1)" },
    ]);
  });

  it("counts sessions, transcripts and events", () => {
    expect(sessionCounts(SESSIONS)).toEqual({ sessions: 3, transcripts: 1, events: 2 });
    expect(sessionCounts([])).toEqual({ sessions: 0, transcripts: 0, events: 0 });
  });

  it("formats a day, and nothing for an unknown one", () => {
    expect(formatDay(FEB_4, { timeZone: "UTC" })).toBe("4 Feb 2026");
    expect(formatDay(null)).toBe("");
  });
});
