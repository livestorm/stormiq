// The v1 Report's vitals and "In short" wording (src/report/vitals.js), on
// the synthetic record fixtures: it formats what the measures send and
// hides what they hide.

import { describe, expect, it } from "vitest";
import small from "./fixtures/record-v1.json";
import crowd from "./fixtures/record-v1-crowd.json";
import { formatPercent, formatSeconds, hiddenReason, inShort, statusFor, vitalCards } from "../src/report/vitals";

const byId = (cards) => Object.fromEntries(cards.map((card) => [card.id, card]));

describe("formatting", () => {
  it("writes percentages and durations", () => {
    expect(formatPercent(0.7143)).toBe("71%");
    expect(formatPercent(null)).toBe("");
    expect(formatSeconds(45)).toBe("45 s");
    expect(formatSeconds(135)).toBe("2 min 15 s");
    expect(formatSeconds(1350)).toBe("22 min 30 s");
    expect(formatSeconds(1800)).toBe("30 min");
    expect(formatSeconds(3900)).toBe("1 h 05 min");
    expect(formatSeconds(null)).toBe("");
  });

  it("maps a measure's state to a status word, and a hidden one to a reason", () => {
    expect(statusFor({ state: "ok" })).toBe("measured");
    expect(statusFor({ state: "ok", coverage: { labels: "model" } })).toBe("estimated");
    expect(statusFor({ state: "partial" })).toBe("estimated");
    expect(statusFor({ state: "approximation" })).toBe("estimated");
    expect(statusFor({ state: "insufficient" })).toBe("");
    expect(hiddenReason({ state: "unavailable", coverage: { missing: ["labels"] } })).toMatch(/Run the analysis/);
    expect(hiddenReason({ state: "unresolved" })).toMatch(/unknown/);
    expect(hiddenReason({ state: "ok" })).toBe("");
  });
});

describe("vitalCards", () => {
  it("shows the crowd session's vitals as the measures send them", () => {
    const cards = byId(vitalCards(crowd.record.measures));
    expect(Object.keys(cards)).toEqual(["show-up", "time-in-room", "wrote", "questions", "answered"]);
    expect(cards["show-up"]).toMatchObject({ value: "75%", sub: "90 of 120 registrants", status: "estimated", hidden: false });
    expect(cards["show-up"].info).toMatch(/Livestorm's dashboard shows 76%/);
    expect(cards["show-up"].info).toMatch(/Excludes your team: \d+\./);
    expect(cards["wrote"]).toMatchObject({ value: "17%", sub: "15 of 90 attendees" });
    expect(cards["time-in-room"].value).toBe("65%");
    expect(cards["time-in-room"].sub).toMatch(/^Median \d+ of \d+ min$/);
  });

  it("hides a gated value and says why", () => {
    const cards = byId(vitalCards(small.record.measures));
    expect(cards["wrote"]).toMatchObject({ value: "—", hidden: true, status: "" });
    expect(cards["wrote"].sub).toBe("Not enough data for a reliable number.");
  });

  it("counts the Q&A until the chat is labelled, then Q&A plus chat questions", () => {
    const before = byId(vitalCards(small.record.measures)).questions;
    expect(before).toMatchObject({ value: "4", sub: "In Q&A only, for now", hidden: false });

    const labelled = {
      ...small.record.measures,
      "m.questions_total": {
        id: "m.questions_total", value: 6, num: null, den: null, state: "ok", coverage: {}, excluded: null, livestorm: null,
        rows: [{ key: "qa", questions: 4 }, { key: "chat", questions: 2 }, { key: "chatLogistics", questions: 1 }],
      },
    };
    const after = byId(vitalCards(labelled)).questions;
    expect(after).toMatchObject({ value: "6", sub: "4 in Q&A · 2 in the chat", status: "measured" });
    expect(after.info).toMatch(/Plus 1 question about the session itself/);
  });

  it("adds the median reply to the answered card's info only when it is shown", () => {
    const cards = byId(vitalCards(small.record.measures));
    expect(cards.answered).toMatchObject({ value: "75%", sub: "3 of 4 questions" });
    const timed = { ...small.record.measures, "m.time_to_answer": { value: 95, state: "ok", den: 3 } };
    expect(byId(vitalCards(timed)).answered.info).toMatch(/Median time to a written answer: 1 min 35 s\./);
  });

  it("never shows a number for a measure that isn't there", () => {
    const cards = vitalCards({});
    expect(cards.every((card) => card.value === "—" && card.hidden)).toBe(true);
  });
});

describe("inShort", () => {
  it("says who came and who wrote, from the measures", () => {
    const short = inShort(crowd.record.measures);
    expect(short.sentence).toBe("90 people came out of 120 registered, and 15 of them wrote in the chat or the Q&A.");
    expect(short.chips.map((chip) => chip.text)).toEqual(["3 questions", "1 still open in Q&A"]);
  });

  it("leaves out what the measures hide", () => {
    const short = inShort(small.record.measures);
    expect(short.sentence).not.toMatch(/wrote/);
    expect(inShort({})).toEqual({ sentence: "", chips: [] });
  });
});
