// The Report's findings: their words (`src/report/findings.js`) against the
// route's fixture and found findings shaped as `record/findings.py` sends
// them, and their loader (`src/report/useFindings.js`).
import { describe, expect, it, vi } from "vitest";
import body from "./fixtures/findings-v1.json";
import { checkedLines, findingsView, foundFinding } from "../src/report/findings";
import { createFindings } from "../src/report/useFindings";

const chapters = {
  id: "chapters",
  state: "found",
  confidence: "estimated",
  strength: 0.6,
  values: {
    high: "Pricing for larger teams",
    low: null,
    chapters: [
      { title: "Welcome", t: 21.9, seconds: 57, qa: false, unprompted: 1, chatters: 1, questions: 0, perMinute: null, index: null },
      { title: "The dashboard", t: 78.9, seconds: 228, qa: false, unprompted: 4, chatters: 3, questions: 1, perMinute: 1.1, index: 100 },
      { title: "Pricing for larger teams", t: 306.9, seconds: 472, qa: false, unprompted: 31, chatters: 7, questions: 1, perMinute: 3.9, index: 355 },
      { title: "Exports and questions", t: 778.9, seconds: 1021, qa: true, unprompted: 10, chatters: 4, questions: 6, perMinute: null, index: null },
    ],
  },
};

const friction = {
  id: "friction",
  state: "found",
  confidence: "estimated",
  strength: 0.8,
  values: {
    incidents: [{ start: 760, end: 850, messages: 4, people: 3, deadAirS: 11.2 }],
    lateStart: { lateS: 420, waitingMessages: 12 },
    speech: true,
  },
};

describe("the findings' words", () => {
  it("say what the route sent for the synthetic webinar: nothing found, and why chapters weren't checked", () => {
    const view = findingsView(body);
    expect(view.found).toEqual([]);
    expect(view.nothing).toEqual(["Production: no burst of sound or connection complaints, and no late start."]);
    expect(view.notChecked).toEqual(["Chapters: too little unprompted chat to compare them."]);
    expect(view.empty).toBe(false);
  });

  it("word a chapter that drew the room in, with a bar per chapter", () => {
    const words = foundFinding(chapters);
    expect(words.headline).toBe("“Pricing for larger teams” drew the room in");
    expect(words.lines[0]).toBe("3.9 unprompted chat messages a minute from 7 people, 3.5× the session's usual chapter.");
    expect(words.bars.map((bar) => [bar.value, bar.tone])).toEqual([
      ["too short", ""],
      ["100", ""],
      ["355", "high"],
      ["6 questions", ""],
    ]);
    expect(words.bars[2]).toMatchObject({ start: "5:07", length: "8 min" });
  });

  it("word a quiet chapter on its own", () => {
    const quiet = { ...chapters, values: { ...chapters.values, high: null, low: "The dashboard" } };
    quiet.values.chapters = quiet.values.chapters.map((row) => (row.title === "The dashboard" ? { ...row, index: 40, perMinute: 0.4 } : row));
    const words = foundFinding(quiet);
    expect(words.headline).toBe("The room went quiet during “The dashboard”");
    expect(words.lines[0]).toBe("During “The dashboard” the chat fell to 0.4 a minute, 0.4× the usual.");
  });

  it("word an incident and a late start", () => {
    const words = foundFinding(friction);
    expect(words.headline).toBe("3 people flagged a sound or connection problem at 12:40");
    expect(words.rows).toEqual([
      "12:40–14:10 · 4 messages from 3 people · 11 s of silence on stage",
      "Started 7 min late · 12 messages in the waiting room",
    ]);
    const late = foundFinding({ ...friction, confidence: "measured", values: { incidents: [], lateStart: { lateS: 300, waitingMessages: 1 }, speech: false } });
    expect(late.headline).toBe("The session started 5 minutes late");
    expect(late.lines).toEqual(["1 message from the audience in the waiting room before it started."]);
    expect(late.confidence).toBe("measured");
  });

  it("keep the server's order and skip what it doesn't know", () => {
    const view = findingsView({ found: [friction, chapters, { id: "unknown", values: {} }], nothing: [], notChecked: [] });
    expect(view.found.map((item) => item.id)).toEqual(["friction", "chapters"]);
    expect(checkedLines({ notChecked: [{ id: "chapters", reason: "nope" }] })).toEqual({ nothing: [], notChecked: [] });
    expect(findingsView(null).empty).toBe(true);
  });
});

describe("the findings' data", () => {
  it("loads one session's findings with its signal, and drops a late answer for a session it left", async () => {
    let release;
    const first = new Promise((resolve) => (release = resolve));
    const other = { ...body, sessionId: "s2" };
    const api = { getSessionFindings: vi.fn((id) => (id === "s1" ? first : Promise.resolve(other))) };
    const findings = createFindings({ api });
    findings.open("s1");
    const signal = api.getSessionFindings.mock.calls[0][1].signal;
    findings.open("s2");
    expect(signal.aborted).toBe(true);
    await vi.waitFor(() => expect(findings.state.body).toBe(other));
    release(body);
    await Promise.resolve();
    expect(findings.state.body).toBe(other);
    findings.close();
  });
});
