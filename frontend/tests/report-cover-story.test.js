// The cover chart's replay (`src/report/coverStory.js`) against the synthetic
// webinar's record, chapters, transcript and moments, which the backend
// generates from its routes.
import { describe, expect, it } from "vitest";
import record from "./fixtures/record-v1.json";
import chapters from "./fixtures/chapters-v1.json";
import transcript from "./fixtures/transcript-v1.json";
import moments from "./fixtures/moments-v1.json";
import {
  LINE_CHARS,
  MAX_WRITTEN,
  SAID_CHARS,
  clip,
  minuteFromKey,
  minuteFromX,
  minuteStory,
  momentAt,
  momentMarks,
  storyIndex,
} from "../src/report/coverStory";

const minutes = record.record.pulse.minutes;
const count = minutes.length;
const story = storyIndex(transcript, count);
const marks = momentMarks(moments, minutes);
const busiest = minutes.reduce((best, row) => (row.audienceChat > best.audienceChat ? row : best), minutes[0]);

function storyOf(index, extra = {}) {
  return minuteStory(index, { minutes, chapters, story, marks, peakMinute: busiest.minute, ...extra });
}

describe("storyIndex", () => {
  it("files speech and the room by live minute, leaving out the time before the start", () => {
    expect(story.speech).toHaveLength(count);
    expect(story.spoken).toBe(true);
    const live = transcript.room.filter(
      (line) => line.t >= 0 && line.t < count * 60 && ((line.kind === "chat" && !line.team) || line.kind === "question"),
    );
    expect(story.written.flat().length + story.asked.flat().length).toBe(live.length);
    for (const [minute, lines] of story.written.entries()) {
      for (const line of lines) expect(Math.floor(line.t / 60)).toBe(minute);
    }
  });

  it("files no speech when the clock doesn't interleave it with the room", () => {
    const apart = storyIndex({ ...transcript, clock: { ...transcript.clock, interleave: false } }, count);
    expect(apart.spoken).toBe(false);
    expect(apart.speech.flat()).toHaveLength(0);
  });

  it("keeps the team's chat when nobody else wrote (an internal session)", () => {
    const internal = { ...transcript, room: transcript.room.map((line) => ({ ...line, team: true })) };
    expect(storyIndex(internal, count).written.flat().length).toBeGreaterThan(0);
  });

  it("is null without a transcript", () => {
    expect(storyIndex(null, count)).toBeNull();
  });
});

describe("minuteStory", () => {
  it("tells the busiest minute: its chapter, counts, and who wrote", () => {
    const card = storyOf(minutes.indexOf(busiest));
    expect(card.peak).toBe(true);
    expect(card.counts).toMatch(new RegExp(`^${busiest.audienceChat} messages?`));
    expect(card.chapter.title).toBeTruthy();
    expect(card.chapter.of).toBe(chapters.chapters.length);
    expect(card.written.length).toBeGreaterThan(0);
    expect(card.written.length).toBeLessThanOrEqual(MAX_WRITTEN);
    expect(card.written.every((line) => line.text.length <= LINE_CHARS + 1 && !/https?:/.test(line.text))).toBe(true);
    expect(card.text).toContain(card.time);
    expect(card.text).toContain("the busiest minute");
  });

  it("quotes the speaker on stage that minute", () => {
    const card = storyOf(story.speech.findIndex((lines) => lines.length > 0));
    expect(card.said.text.length).toBeLessThanOrEqual(SAID_CHARS + 1);
    expect(transcript.speakers.map((s) => s.name)).toContain(card.said.speaker);
  });

  it("says nothing of the speech, the messages or the moment without the reads", () => {
    const card = minuteStory(0, { minutes, chapters: null });
    expect(card).toMatchObject({ said: null, written: [], asked: [], chapter: null, moment: null });
  });

  it("calls a minute with nothing written quiet", () => {
    const index = minutes.findIndex((row) => !row.audienceChat && !row.questions);
    if (index >= 0) expect(storyOf(index)).toMatchObject({ quiet: true, counts: "" });
  });
});

describe("momentMarks", () => {
  it("pins each moment on the ridge at its busiest minute", () => {
    expect(marks.map((mark) => mark.n)).toEqual(moments.moments.map((moment) => moment.n));
    for (const mark of marks) {
      expect(mark.left).toBeCloseTo(((mark.minute + 0.5) / count) * 100);
      expect(mark.top).toBeGreaterThanOrEqual(6);
      expect(mark.top).toBeLessThanOrEqual(99);
      expect(mark.label).toMatch(/^Key moment \d+, /);
      expect(momentAt(marks, mark.minute)).toBe(mark);
    }
  });

  it("pins nothing before the moments are ready", () => {
    expect(momentMarks(null, minutes)).toEqual([]);
    expect(momentMarks({ state: "few_messages", moments: [] }, minutes)).toEqual([]);
  });
});

describe("moving across the chart", () => {
  it("snaps a pointer to the minute under it", () => {
    expect(minuteFromX(0, 600, 30)).toBe(0);
    expect(minuteFromX(599, 600, 30)).toBe(29);
    expect(minuteFromX(700, 600, 30)).toBe(29);
    expect(minuteFromX(-5, 600, 30)).toBe(0);
  });

  it("moves by a minute, five minutes, or to either end with the keys", () => {
    expect(minuteFromKey("ArrowRight", null, 30)).toBe(0);
    expect(minuteFromKey("ArrowLeft", null, 30)).toBe(29);
    expect(minuteFromKey("ArrowRight", 3, 30)).toBe(4);
    expect(minuteFromKey("PageUp", 27, 30)).toBe(29);
    expect(minuteFromKey("PageDown", 2, 30)).toBe(0);
    expect(minuteFromKey("Home", 12, 30)).toBe(0);
    expect(minuteFromKey("End", 12, 30)).toBe(29);
    expect(minuteFromKey("Tab", 12, 30)).toBeNull();
  });
});

describe("clip", () => {
  it("cuts at a word with an ellipsis, and leaves short text alone", () => {
    expect(clip("short", 10)).toBe("short");
    expect(clip("the export keeps every filter we set", 20)).toBe("the export keeps…");
  });
});
