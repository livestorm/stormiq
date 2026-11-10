// The v1 Sessions page's rows, filters and counts (src/sessions/list.js).

import { describe, expect, it } from "vitest";
import { eventOptions, formatDay, sessionCounts, sessionRow, visibleSessions } from "../src/sessions/list";

const MARCH_12 = Date.UTC(2026, 2, 12, 14, 0) / 1000;
const FEB_4 = Date.UTC(2026, 1, 4, 10, 0) / 1000;

const SESSIONS = [
  {
    sessionId: "s-1", sessionName: "Spring release tour", eventId: "e-1", eventTitle: "Release tours",
    startedAtUnix: MARCH_12, durationSeconds: 2700, attendeesCount: 251, registrantsCount: 600,
    hasTranscript: true, generatedByName: "Camille Martin", generatedAtIso: "2026-03-13T09:00:00+00:00",
  },
  {
    sessionId: "s-2", sessionName: "Partner kickoff", eventId: "e-2", eventTitle: "Partner kickoff",
    startedAtUnix: FEB_4, durationSeconds: 3000, attendeesCount: 64, registrantsCount: null,
    hasTranscript: false, generatedByEmail: "hugo@example.test", generatedAtIso: "2026-03-20T09:00:00+00:00",
  },
  {
    sessionId: "s/3", sessionName: "", eventId: "e-1", eventTitle: "Release tours",
    startedAtUnix: null, durationSeconds: null, attendeesCount: undefined, hasTranscript: false,
  },
];

describe("sessionRow", () => {
  it("names a session by its event, with its own name under it, and words every cell", () => {
    expect(sessionRow(SESSIONS[0], { timeZone: "UTC" })).toEqual({
      id: "s-1", href: "/sessions/s-1", title: "Release tours", subtitle: "Spring release tour", length: "45 min",
      day: "12 Mar 2026", attendees: "251", registrants: "of 600", transcript: "ready", addedBy: "Camille Martin",
    });
  });

  it("doesn't repeat a name that is the event's, and falls back to the email", () => {
    expect(sessionRow(SESSIONS[1], { timeZone: "UTC" })).toMatchObject({
      title: "Partner kickoff", subtitle: "", registrants: "", transcript: "none", addedBy: "hugo@example.test",
    });
  });

  it("never shows the route's placeholder as a name, and encodes the id", () => {
    expect(sessionRow({ ...SESSIONS[2], sessionName: "Untitled session" })).toMatchObject({
      title: "Release tours", subtitle: "", day: "—", attendees: "—", length: "", href: "/sessions/s%2F3",
    });
    expect(sessionRow({ sessionId: "x", sessionName: "Spring tour" }).title).toBe("Spring tour");
    expect(sessionRow({ sessionId: "x", sessionName: "Untitled session" }).title).toBe("Untitled session");
  });
});

describe("visibleSessions", () => {
  it("sorts by session date, newest first, by default; unknown dates last", () => {
    expect(visibleSessions(SESSIONS).map((s) => s.sessionId)).toEqual(["s-1", "s-2", "s/3"]);
    expect(visibleSessions(SESSIONS, { sort: "sessionAsc" }).map((s) => s.sessionId)).toEqual(["s-2", "s-1", "s/3"]);
  });

  it("filters by event and searches titles and who added it", () => {
    expect(visibleSessions(SESSIONS, { event: "e-1" }).map((s) => s.sessionId)).toEqual(["s-1", "s/3"]);
    expect(visibleSessions(SESSIONS, { query: "  HUGO " }).map((s) => s.sessionId)).toEqual(["s-2"]);
    expect(visibleSessions(SESSIONS, { query: "release" }).map((s) => s.sessionId)).toEqual(["s-1", "s/3"]);
  });

  it("sorts by when it was added, or by title", () => {
    expect(visibleSessions(SESSIONS, { sort: "addedDesc" }).map((s) => s.sessionId)).toEqual(["s-2", "s-1", "s/3"]);
    // By the title shown (the event's): Partner kickoff, then the two Release tours, newest first.
    expect(visibleSessions(SESSIONS, { sort: "titleAsc" }).map((s) => s.sessionId)).toEqual(["s-2", "s-1", "s/3"]);
  });

  it("never changes the list it is given", () => {
    const before = SESSIONS.map((s) => s.sessionId);
    visibleSessions(SESSIONS, { sort: "titleAsc" });
    expect(SESSIONS.map((s) => s.sessionId)).toEqual(before);
  });
});

describe("eventOptions and sessionCounts", () => {
  it("lists events, the busiest first", () => {
    expect(eventOptions(SESSIONS)).toEqual([
      { value: "e-1", label: "Release tours (2)" },
      { value: "e-2", label: "Partner kickoff (1)" },
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
