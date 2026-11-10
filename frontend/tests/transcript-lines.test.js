import { describe, expect, it } from "vitest";
import body from "./fixtures/transcript-v1.json";
import { speakersOf, subtitles, textParts, transcriptLines } from "../src/transcript/lines";

const apart = { ...body, clock: { interleave: false, offsetS: null }, speech: body.speech.map((line) => ({ ...line, t: null })) };

describe("the Transcript tab's lines", () => {
  it("put speech and the room on one clock when the server placed the speech", () => {
    const shown = transcriptLines(body);
    expect(shown.interleaved).toBe(true);
    expect(shown.lines).toHaveLength(body.speech.length + body.room.length);
    expect(shown.speech).toEqual([]);
    const times = shown.lines.map((line) => line.t ?? Infinity);
    expect(times).toEqual([...times].sort((a, b) => a - b));
    // Speech times come from the server, never from r + offset worked out here.
    const first = shown.lines.find((line) => line.kind === "speech");
    expect(first.t).toBe(body.speech[0].t);
  });

  it("show speech and the room apart, on the recording's times, when it didn't", () => {
    const shown = transcriptLines(apart);
    expect(shown.interleaved).toBe(false);
    expect(shown.lines).toEqual([]);
    expect(shown.speech.map((line) => line.id)).toEqual(body.speech.map((line) => line.id));
    expect(shown.speech.every((line) => line.t === null && line.timeIsRecording)).toBe(true);
    expect(shown.speech[0].time).toBe("0:03");
    expect(shown.room.map((line) => line.id)).toEqual(body.room.map((line) => line.id));
  });

  it("word the room's lines: author, source, before the start, written answers", () => {
    const room = transcriptLines(body, { view: "room" }).lines;
    expect(room.every((line) => line.kind !== "speech")).toBe(true);
    const before = room.find((line) => line.t < 0);
    expect(before.time).toBe("Before");
    const question = room.find((line) => line.kind === "question");
    expect(question.source).toBe("Q&A");
    expect(room.find((line) => line.kind === "question" && line.status === "Answered in writing")).toBeTruthy();
    expect(room.every((line) => line.who)).toBe(true);
  });

  it("filter by view, search and speaker", () => {
    expect(transcriptLines(body, { view: "speech" }).lines.every((line) => line.kind === "speech")).toBe(true);
    const found = transcriptLines(body, { query: "DASHBOARD" }).lines;
    expect(found.length).toBeGreaterThan(0);
    expect(found.every((line) => `${line.text} ${line.who}`.toLowerCase().includes("dashboard"))).toBe(true);
    const nora = transcriptLines(body, { speaker: "0" });
    expect(nora.lines.length).toBeGreaterThan(0);
    expect(nora.lines.every((line) => line.kind === "speech" && line.speaker === "0")).toBe(true);
    expect(nora.counts.room).toBe(0);
  });

  it("cut a line at its unclear words, and ignore spans that don't fit", () => {
    expect(textParts("paste the metadata URL", [[10, 18]])).toEqual([
      { text: "paste the ", unclear: false },
      { text: "metadata", unclear: true },
      { text: " URL", unclear: false },
    ]);
    expect(textParts("short", [[2, 40], [3, 1], [-1, 2]])).toEqual([{ text: "short", unclear: false }]);
    // Unclear words next to each other are one part.
    expect(textParts("we walk on", [[0, 2], [3, 7]])).toEqual([
      { text: "we walk", unclear: true },
      { text: " on", unclear: false },
    ]);
    expect(textParts("", [])).toEqual([{ text: "", unclear: false }]);
    const unclear = transcriptLines(body).lines.find((line) => line.parts.some((part) => part.unclear));
    expect(unclear.parts.map((part) => part.text).join("")).toBe(unclear.text);
  });

  it("name a speaker once per stretch of their lines", () => {
    const lines = transcriptLines(body, { view: "speech" }).lines;
    expect(lines[0].continued).toBeUndefined();
    expect(lines[1].continued).toBe(true);
    const change = lines.findIndex((line, index) => index > 0 && line.speaker !== lines[index - 1].speaker);
    expect(lines[change].continued).toBeUndefined();
  });

  it("name the speakers as saved, then by order", () => {
    expect(speakersOf(body)).toEqual([
      { key: "0", name: "Nora Vale", named: true },
      { key: "1", name: "Rhea Stone", named: true },
      { key: "2", name: "Speaker 3", named: false },
    ]);
  });
});

describe("the subtitles", () => {
  it("are SubRip cues on the recording's own times", () => {
    const srt = subtitles(body);
    const cues = srt.trim().split("\n\n");
    expect(cues).toHaveLength(body.speech.length);
    expect(cues[0]).toBe("1\n00:00:03,000 --> 00:00:06,050\nNora Vale: welcome everyone thanks for joining us today");
  });

  it("give a cue without an end until the next one starts", () => {
    const srt = subtitles({
      speakers: [],
      speech: [
        { id: "U1", r: 1, rEnd: null, speaker: null, text: "one" },
        { id: "U2", r: 3725.5, rEnd: 3726, speaker: null, text: "two\nlines" },
        { id: "U3", r: null, speaker: null, text: "no time" },
      ],
    });
    expect(srt).toBe("1\n00:00:01,000 --> 01:02:05,500\nSpeaker: one\n\n2\n01:02:05,500 --> 01:02:06,000\nSpeaker: two lines\n");
  });
});
