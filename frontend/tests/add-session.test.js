import { describe, expect, it } from "vitest";
import { EVENT_STATUSES, addedSessionIds, eventRow, finishedLine, idFromInput, sessionChoice, sessionListParts, statusLabel } from "../src/sessions/add";

const ID = "3f2a1b4c-5d6e-4f70-8a9b-0c1d2e3f4a5b";
const OTHER = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";

describe("the events", () => {
  it("word a status, a known one by its label", () => {
    expect(statusLabel("on_demand")).toBe("On demand");
    expect(statusLabel("ended")).toBe("Ended");
    expect(statusLabel("paused_for_now")).toBe("Paused for now");
    expect(statusLabel("")).toBe("");
    expect(EVENT_STATUSES[0]).toEqual({ value: "", label: "All statuses" });
  });

  it("word a row, and leave out a count Livestorm doesn't give", () => {
    expect(eventRow({ id: "e1", title: "Monthly webinar", scheduling_status: "ended", sessions_count: "12" })).toEqual({
      id: "e1",
      title: "Monthly webinar",
      status: "Ended",
      sessions: "12 sessions",
      count: 12,
    });
    expect(eventRow({ id: "e2", title: "", sessions_count: "1" }).sessions).toBe("1 session");
    expect(eventRow({ id: "e2", title: "" }).title).toBe("Untitled event");
    expect(eventRow({ id: "e3", sessions_count: "n/a" }).sessions).toBe("");
  });
});

describe("an event's sessions", () => {
  it("say how many finished, out of all the event has", () => {
    expect(finishedLine(2, 6)).toBe(
      "2 finished sessions of 6: the others are still to come, never started, cancelled or drafts, and can't be added.",
    );
    expect(finishedLine(6, 6)).toBe("6 finished sessions");
    expect(finishedLine(1, null)).toBe("1 finished session");
    expect(finishedLine(null, 6)).toBe("");
  });

  it("word a session from the route's raw fields", () => {
    const row = sessionChoice({ id: ID, name: "Spring launch", startedAt: 1_700_000_000, attendees: 42 }, new Set(), {
      timeZone: "UTC",
    });
    expect(row).toEqual({
      id: ID,
      title: "Spring launch",
      day: "14 Nov 2023",
      attendees: "42 attendees",
      added: false,
      href: `/sessions/${ID}`,
    });
  });

  it("name an unnamed session and leave out what's unknown", () => {
    const row = sessionChoice({ id: "s1", name: "", startedAt: null, attendees: null });
    expect(row.title).toBe("Untitled session");
    expect(row.day).toBe("");
    expect(row.attendees).toBe("");
    expect(sessionChoice({ id: "s1", attendees: 1 }).attendees).toBe("1 attendee");
  });

  it("mark a session the organization already has", () => {
    const added = addedSessionIds([{ sessionId: ID }, { sessionId: "" }, {}]);
    expect([...added]).toEqual([ID]);
    expect(sessionChoice({ id: ID }, added).added).toBe(true);
    expect(sessionChoice({ id: OTHER }, added).added).toBe(false);
  });
});

describe("a pasted link or ID", () => {
  it("finds the one ID in a link", () => {
    expect(idFromInput(`https://app.livestorm.co/sessions/${ID}/report`)).toEqual({ id: ID, problem: "" });
    expect(idFromInput(`  ${ID.toUpperCase()}  `)).toEqual({ id: ID, problem: "" });
    expect(idFromInput(`${ID} and ${ID}`)).toEqual({ id: ID, problem: "" });
  });

  it("won't guess between two IDs", () => {
    expect(idFromInput(`https://app.livestorm.co/events/${OTHER}/sessions/${ID}`)).toEqual({ id: "", problem: "many" });
  });

  it("takes a bare value as it is, and nothing from text with no ID", () => {
    expect(idFromInput("abc123")).toEqual({ id: "abc123", problem: "" });
    expect(idFromInput("")).toEqual({ id: "", problem: "empty" });
    expect(idFromInput("https://example.com/nothing")).toEqual({ id: "", problem: "empty" });
    expect(idFromInput("two words")).toEqual({ id: "", problem: "empty" });
  });
});

describe("an open event's sessions", () => {
  it("show the count line above the list, never instead of it", () => {
    const sessions = [{ id: "s1" }, { id: "s2" }];
    expect(sessionListParts({ ready: true, sessions, total: 337, all: 359 })).toEqual({
      loading: false,
      empty: false,
      countLine: "337 finished sessions of 359: the others are still to come, never started, cancelled or drafts, and can't be added.",
      list: true,
    });
    expect(sessionListParts({ ready: true, sessions, total: 2, all: 2 })).toMatchObject({ countLine: "2 finished sessions", list: true });
    expect(sessionListParts({ ready: true, sessions, total: null, all: null })).toMatchObject({ list: true });
    expect(sessionListParts({ ready: true, sessions: [], total: 0, all: 4 })).toMatchObject({ empty: true, list: false });
    expect(sessionListParts({ ready: false, sessions })).toMatchObject({ loading: true, list: false });
  });
});
