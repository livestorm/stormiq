// The Report's cover chart (`src/report/cover.js`) against the synthetic
// webinar's record and chapters, which the backend generates from its routes.
import { describe, expect, it } from "vitest";
import record from "./fixtures/record-v1.json";
import chapters from "./fixtures/chapters-v1.json";
import { chapterAt, chapterSegments, coverModel, placedChapters } from "../src/report/cover";

const minutes = record.record.pulse.minutes;

describe("coverModel", () => {
  it("marks the busiest minute and says which chapter it fell in", () => {
    const model = coverModel(minutes, chapters);
    const most = Math.max(...minutes.map((row) => row.audienceChat));
    const minute = minutes.find((row) => row.audienceChat === most).minute;
    expect(model.peak.count).toBe(most);
    expect(model.peak.left).toBeCloseTo(((minute + 0.5) / minutes.length) * 100);
    expect(model.peak.chapter).toBe(chapterAt(placedChapters(chapters), minute * 60 + 30).title);
    expect(model.glance[0]).toMatchObject({ label: "Busiest minute", value: model.peak.time });
  });

  it("dots every minute with questions", () => {
    const model = coverModel(minutes, chapters);
    expect(model.questions).toHaveLength(minutes.filter((row) => row.questions > 0).length);
    expect(model.questions[0].title).toMatch(/question/);
  });

  it("lays the chapters end to end and names the liveliest and the most asked", () => {
    const model = coverModel(minutes, chapters);
    expect(model.segments.map((s) => s.title)).toEqual(chapters.chapters.map((c) => c.title));
    for (const [index, segment] of model.segments.slice(0, -1).entries()) {
      expect(segment.left + segment.width).toBeCloseTo(model.segments[index + 1].left);
    }
    expect(model.segments.at(-1).left + model.segments.at(-1).width).toBeCloseTo(100);
    const lively = chapters.chapters.reduce((best, c) => (c.chatPerMinute > best.chatPerMinute ? c : best));
    expect(model.glance.find((item) => item.label === "Liveliest chapter").value).toBe(lively.title);
    expect(model.lively).toBe(model.segments[chapters.chapters.indexOf(lively)].key);
    // Three chapters with one question each: no chapter leads, so nothing is said.
    expect(model.glance.find((item) => item.label === "Most questions")).toBeUndefined();
    const asked = { ...chapters, chapters: chapters.chapters.map((c, i) => (i === 3 ? { ...c, questions: 4 } : c)) };
    expect(coverModel(minutes, asked).glance.find((item) => item.label === "Most questions")).toMatchObject({
      value: "Exports and questions",
      detail: "4 questions",
    });
    expect(model.description).toContain("chapters:");
  });

  it("draws no chapter the server didn't place, and says when nobody wrote", () => {
    expect(coverModel(minutes, { ...chapters, placed: false }).segments).toEqual([]);
    expect(coverModel(minutes, { ...chapters, state: "stale" }).segments).toEqual([]);
    expect(coverModel(minutes, null).glance.map((item) => item.label)).toEqual(["Busiest minute"]);
    const quiet = coverModel([{ minute: 0, audienceChat: 0, questions: 0 }], null);
    expect(quiet.peak).toBeNull();
    expect(quiet.glance).toEqual([]);
    expect(quiet.description).toContain("nobody in the audience wrote");
  });

  it("holds a segment inside the axis", () => {
    const [segment] = chapterSegments([{ title: "Late", t: 99999 }], 10);
    expect(segment).toMatchObject({ left: 100, width: 0 });
  });
});
