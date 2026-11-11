// The Report's findings: their words (`src/report/findings.js`) against the
// route's fixture and found findings shaped as `record/findings.py` sends
// them, and their loader (`src/report/useFindings.js`).
import { describe, expect, it, vi } from "vitest";
import body from "./fixtures/findings-v1.json";
import { checkedLines, findingsView, foundFinding, pulseMarks } from "../src/report/findings";
import { createFindings } from "../src/report/useFindings";

const chapters = {
  id: "chapters",
  state: "found",
  confidence: "estimated",
  strength: 0.6,
  values: {
    high: "Pricing for larger teams",
    low: null,
    usualPerMinute: 1.1,
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
    expect(view.nothing).toEqual([
      "Production: no burst of sound or connection complaints, and no late start.",
      "Monologues: the longest stretch without bringing the room in was 5 min.",
    ]);
    expect(view.notChecked).toEqual([
      "Chapters: too little unprompted chat to compare them.",
      "Prompts to the room: the analysis found none in the talk.",
      "Moments: fewer than 30 audience messages, too few to find a burst.",
    ]);
    expect(view.empty).toBe(false);
  });

  it("word a chapter that drew the room in, with a chart in messages a minute", () => {
    const words = foundFinding(chapters);
    expect(words.headline).toBe("“Pricing for larger teams” drew the room in");
    expect(words.lines[0]).toBe("3.9 chat messages a minute from 7 people, 3.5× the usual chapter (1.1 a minute).");
    expect(words.chart.title).toBe("The room's own chat, messages a minute, by chapter");
    expect(words.chart.usual).toBe("usual: 1.1 a minute");
    expect(words.chart.rows.map((row) => [row.value, row.tone])).toEqual([
      ["under 2 min", ""],
      ["1.1/min", ""],
      ["3.9/min", "high"],
      ["Q&A · 6 questions", ""],
    ]);
    expect(words.chart.rows[2]).toMatchObject({ when: "5:07 · 8 min", width: 100 });
    expect(words.chart.usualLeft).toBeCloseTo((1.1 / 3.9) * 100);
  });

  it("word a quiet chapter on its own", () => {
    const quiet = { ...chapters, values: { ...chapters.values, high: null, low: "The dashboard" } };
    quiet.values.chapters = quiet.values.chapters.map((row) => (row.title === "The dashboard" ? { ...row, index: 40, perMinute: 0.4 } : row));
    const words = foundFinding(quiet);
    expect(words.headline).toBe("The room went quiet during “The dashboard”");
    expect(words.lines[0]).toBe("During “The dashboard” the chat fell to 0.4 a minute, 0.4× the usual.");
  });

  it("leave the people out when the chat's authors couldn't be told apart", () => {
    const unknown = { ...friction, values: { ...friction.values, lateStart: null, incidents: [{ start: 75, end: 355, messages: 17, people: 0, deadAirS: 16 }] } };
    const words = foundFinding(unknown);
    expect(words.headline).toBe("17 messages about sound or connection from 1:15");
    expect(words.rows).toEqual(["1:15–5:55 · 17 messages · 16 s of silence on stage"]);
    expect(words.headline + words.rows.join(" ")).not.toMatch(/0 people/);
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

const monologue = {
  id: "monologue",
  state: "found",
  confidence: "estimated",
  strength: 0.5,
  values: { start: 600, end: 1680, seconds: 1080, firstPerMinute: 2.1, lastPerMinute: 0.6, first: 13, last: 4 },
};

const prompts = {
  id: "prompts",
  state: "found",
  confidence: "estimated",
  strength: 0.65,
  values: {
    missed: [306.9],
    prompts: [
      { t: 306.9, quote: "Any questions so far?", waitS: 6.2, messages: 8, expected: 0.7, excess: 7, late: 6, medianDelayS: 24 },
      { t: 900, quote: "Tell me in the chat", waitS: 25, messages: 5, expected: 0.7, excess: 4, late: 0, medianDelayS: 9 },
    ],
  },
};

describe("monologues and prompts", () => {
  it("word a long stretch where the chat fell", () => {
    const words = foundFinding(monologue);
    expect(words.headline).toBe("18 minutes without bringing the room in, and the chat fell");
    expect(words.lines[0]).toContain("From 10:00 to 28:00");
    expect(words.lines[0]).toContain("from 2.1 to 0.6 messages a minute");
  });

  it("word the prompts the room answered too late, one row each", () => {
    const words = foundFinding(prompts);
    expect(words.headline).toBe("The room answered after the speaker had moved on");
    expect(words.rows).toEqual([
      "5:07 “Any questions so far?” · waited 6 s · 8 messages in the next 2 min (about 1 usual) · 6 after the speaker went on",
    ]);
  });

  it("say what was checked when nothing stood out", () => {
    const lines = checkedLines({
      nothing: [
        { id: "monologue", values: { start: 0, end: 300, seconds: 297.5 } },
        { id: "monologue", values: { seconds: 700, firstPerMinute: 1, lastPerMinute: 1 } },
        { id: "prompts", values: { prompts: [prompts.values.prompts[1]] } },
      ],
      notChecked: [{ id: "prompts", reason: "clock" }],
    });
    expect(lines.nothing).toEqual([
      "Monologues: the longest stretch without bringing the room in was 5 min.",
      "Monologues: the longest stretch ran 12 min, and the chat held up.",
      "Prompts to the room: it had time to answer the one prompt.",
    ]);
    expect(lines.notChecked).toEqual(["Prompts to the room: needs the recording's clock checked to the second."]);
  });

  it("mark the stretch and every prompt on the Pulse", () => {
    const marks = pulseMarks({ found: [monologue, prompts], nothing: [] }, 30);
    expect(marks.bands).toEqual([
      { left: (600 / 1800) * 100, width: (1080 / 1800) * 100, label: "18 min, no prompt · chat 2.1 → 0.6/min" },
    ]);
    expect(marks.prompts.map((mark) => [Math.round(mark.left * 10) / 10, mark.missed])).toEqual([
      [17, true],
      [50, false],
    ]);
    expect(pulseMarks(null, 30)).toEqual({ bands: [], prompts: [], pins: [] });
  });
});

const moments = {
  id: "moments",
  state: "found",
  confidence: "estimated",
  strength: 0.6,
  values: {
    threshold: 5,
    usualPerMinute: 2,
    replies: 1,
    waves: 1,
    lagS: 15,
    quotes: true,
    moments: [
      { n: 1, start: 600, end: 660, messages: 14, people: 9, peak: 14, quote: "and that's the price for a whole team", utterance: "U0060" },
      { n: 2, start: 1200, end: 1320, messages: 20, people: 11, peak: 12, quote: null, utterance: null },
    ],
  },
};

describe("unprompted moments", () => {
  it("word each moment with the words being said, never a cause", () => {
    const words = foundFinding(moments);
    expect(words.headline).toBe("The room leaned in 2 times, unprompted");
    expect(words.rows).toEqual([
      "1. 10:00–11:00 · 14 messages from 9 people · 7× the usual minute · while the speaker was saying “and that's the price for a whole team”",
      "2. 20:00–22:00 · 20 messages from 11 people · 6× the usual minute",
    ]);
    expect(words.lines.join(" ")).not.toMatch(/because/);
    const one = foundFinding({ ...moments, values: { ...moments.values, quotes: false, moments: [moments.values.moments[0]] } });
    expect(one.headline).toBe("The room leaned in at 10:00, unprompted");
    expect(one.lines[1]).toMatch(/isn't checked to the second/);
  });

  it("pin each moment on the Pulse, and say what else the bursts were", () => {
    const { pins } = pulseMarks({ found: [moments] }, 30);
    expect(pins.map((pin) => [pin.n, Math.round(pin.left * 10) / 10])).toEqual([
      [1, 35],
      [2, 70],
    ]);
    expect(checkedLines({ nothing: [{ id: "moments", values: { usualPerMinute: 2, replies: 1, waves: 2 } }] }).nothing).toEqual([
      "Moments: no burst beyond the room's usual 2 a minute, other than 1 reply to a prompt and 2 waves of greetings or logistics.",
    ]);
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
