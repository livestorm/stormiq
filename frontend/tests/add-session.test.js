import { describe, expect, it } from "vitest";
import { EVENT_STATUSES, addedSessionIds, eventRow, idFromInput, sessionChoice, statusLabel } from "../src/sessions/add";

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
    });
    expect(eventRow({ id: "e2", title: "", sessions_count: "1" }).sessions).toBe("1 session");
    expect(eventRow({ id: "e2", title: "" }).title).toBe("Untitled event");
    expect(eventRow({ id: "e3", sessions_count: "n/a" }).sessions).toBe("");
  });
});

describe("an event's sessions", () => {
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
