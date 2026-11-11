// The "In short" card's points (src/report/summary.js): the strongest
// findings, the moment the room reacted most and the questions still open,
// from the findings, moments and desk fixtures the backend generates, and
// bodies shaped the same way.

import { describe, expect, it } from "vitest";
import desk from "./fixtures/questions-v1.json";
import { shortPoints } from "../src/report/summary";

const friction = { id: "friction", state: "found", confidence: "measured", strength: 0.8, values: { lateStart: { lateS: 420, waiting: 12 }, incidents: [] } };
const moment = (over) => ({
  n: 1, start: 540, end: 660, kind: "content", messages: 21, people: 11, questions: 2, peak: 13, prompt: null,
  said: { t: 515, speakers: ["Nora Vale"], text: "the export now keeps every filter you set", precise: true },
  written: [], asked: [], themes: {}, ...over,
});

describe("shortPoints", () => {
  it("says the strongest finding, the moment the room reacted most and the open questions", () => {
    const points = shortPoints({
      findings: { found: [friction], nothing: [], notChecked: [] },
      moments: { moments: [moment({ kind: "social", messages: 40 }), moment()] },
      desk,
    });
    expect(points).toEqual([
      { icon: "target", text: "The session started 7 minutes late." },
      {
        icon: "zap",
        text: "The room reacted most at 9:00–11:00: 21 messages from 11 people, while Nora Vale was saying “the export now keeps every filter you set”.",
      },
      { icon: "help-circle", text: "2 questions are still open: answer them on Follow-up." },
    ]);
  });

  it("names the stage's prompt for a reply, and says when every question was answered", () => {
    const reply = moment({ kind: "reply", prompt: { t: 500, quote: "What would you use this for first?" } });
    const answered = { summary: { total: 3, written: 3, open: 0, covered: 0 } };
    expect(shortPoints({ moments: { moments: [reply] }, desk: answered })).toEqual([
      { icon: "zap", text: "The room reacted most at 9:00–11:00: 21 messages from 11 people, answering “What would you use this for first?”." },
      { icon: "check", text: "All 3 questions were answered." },
    ]);
  });

  it("is empty before anything loads", () => {
    expect(shortPoints()).toEqual([]);
    expect(shortPoints({ moments: { moments: [moment({ kind: "tech" })] } })).toEqual([]);
  });
});
