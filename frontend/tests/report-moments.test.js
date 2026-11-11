// The Pulse's key moments, told (src/report/moments.js), against
// `tests/fixtures/moments-v1.json`, which the backend generates from the real
// route, and a richer body shaped the same way; and the Pulse's pins taken
// from them (src/report/findings.js `pulseMarks`).

import { describe, expect, it } from "vitest";
import fixture from "./fixtures/moments-v1.json";
import { momentPins, momentView, momentsView } from "../src/report/moments";
import { pulseMarks } from "../src/report/findings";

const reply = {
  n: 2, start: 900, end: 1020, kind: "reply", messages: 18, people: 9, questions: 1, peak: 11,
  prompt: { t: 890, quote: "Tell us how you export today" },
  said: { t: 885, speakers: ["Nora Vale"], text: "So, tell us how you export today", precise: true },
  written: [{ id: "C0100", t: 905, text: "We copy it by hand", author: "Mila F." }],
  asked: [{ id: "Q010", t: 930, text: "Does it keep the filters?", author: null }],
  themes: { content: 12, question: 1, social: 2 },
};

describe("momentsView", () => {
  it("tells the fixture's busiest minute, with what was said and what people wrote", () => {
    const view = momentsView(fixture);
    expect(view.intro).toMatch(/^No minute stood far above the rest; these were the busiest/);
    const moment = view.moments[0];
    expect(moment).toMatchObject({ n: 1, when: "1:00–2:00", kind: "Greetings and thanks" });
    expect(moment.said.label).toMatch(/^At \d+:\d\d, .+ was saying:$/);
    expect(moment.written.map((line) => line.author)).toEqual(fixture.moments[0].written.map((line) => line.author || "An attendee"));
  });

  it("words a reply with the stage's prompt, the questions and the chat's themes", () => {
    const moment = momentView(reply);
    expect(moment).toMatchObject({
      when: "15:00–17:00",
      kind: "Answering the stage",
      takeaway: "The room answered a question from the stage.",
      facts: "18 messages from 9 people · 1 question · up to 11 a minute",
      prompt: { time: "14:50", text: "Tell us how you export today" },
    });
    expect(moment.said).toBeNull();
    expect(moment.asked).toEqual([{ id: "Q010", time: "15:30", author: "An attendee", text: "Does it keep the filters?" }]);
    expect(moment.themes).toBe("In the chat: 12 reactions to the content, 2 greetings and thanks, 1 question.");
  });

  it("says around when the clock is only good to the minute, and why speech is missing", () => {
    const loose = momentView({ ...reply, kind: "content", prompt: null, said: { ...reply.said, precise: false } });
    expect(loose.said.label).toBe("Around 14:45, Nora Vale was saying:");
    const view = momentsView({ ...fixture, speech: false, surges: true, usualPerMinute: 2, moments: [reply] });
    expect(view.intro).toBe(
      "The 1 moment the chat jumped well above its usual pace, numbered as on the chart. The chat usually ran at 2 messages a minute. What was said isn't shown: the recording couldn't be placed on the session clock.",
    );
  });

  it("says why there is nothing to tell", () => {
    expect(momentsView({ state: "few_messages" }).empty).toBe("Too few messages in the live chat to pick out moments.");
    expect(momentsView({ state: "ready", moments: [] }).empty).toBe("No minute stood out in the chat.");
    expect(momentsView(null)).toBeNull();
  });
});

describe("the Pulse's pins", () => {
  it("number the told moments where they are on the chart", () => {
    const pins = momentPins({ moments: [reply] });
    expect(pins).toEqual([{ n: 2, start: 900, end: 1020, title: "2. 15:00–17:00: Answering the stage, 18 messages" }]);
    const marks = pulseMarks({ found: [], nothing: [] }, 30, pins);
    expect(marks.pins).toHaveLength(1);
    expect(marks.pins[0]).toMatchObject({ n: 2, title: pins[0].title });
    expect(marks.pins[0].left).toBeGreaterThan(45);
    expect(marks.pins[0].left).toBeLessThan(60);
  });

  it("fall back on the findings' moments before the told ones load", () => {
    const body = { found: [{ id: "moments", values: { moments: [{ n: 1, start: 600, end: 660, messages: 9, quote: null }] } }] };
    expect(pulseMarks(body, 30).pins.map((pin) => pin.n)).toEqual([1]);
  });
});
