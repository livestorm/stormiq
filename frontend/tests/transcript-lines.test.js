import { describe, expect, it } from "vitest";
import withChapters from "./fixtures/transcript-v1.json";
import { ROOM_KINDS, SPEAKER_TONES, authorsOf, speakersOf, subtitles, textParts, transcriptLines } from "../src/transcript/lines";

// The lines' own rules, on the fixture without its chapters (tested below).
const body = { ...withChapters, chapters: { state: "none", items: [] } };
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
      { key: "0", name: "Nora Vale", named: true, tone: 1 },
      { key: "1", name: "Rhea Stone", named: true, tone: 2 },
      { key: "2", name: "Speaker 3", named: false, tone: 3 },
    ]);
  });

  it("give each speaker's lines the speaker's colour, reusing them past the last", () => {
    const shown = transcriptLines(apart);
    const tones = Object.fromEntries(shown.speech.map((line) => [line.speaker, line.tone]));
    expect(tones).toEqual({ 0: 1, 1: 2, 2: 3 });
    const many = { speakers: Array.from({ length: SPEAKER_TONES + 1 }, (_, i) => ({ key: String(i), name: `S${i}` })) };
    expect(speakersOf(many).map((s) => s.tone).slice(-2)).toEqual([SPEAKER_TONES, 1]);
  });
});

describe("the room's chat, its questions, or both", () => {
  const room = (roomKind, source = apart) => transcriptLines(source, { roomKind }).room;

  it("offers both, the chat and the questions, both first", () => {
    expect(ROOM_KINDS.map((kind) => kind.value)).toEqual(["all", "chat", "questions"]);
  });

  it("keeps only the chat, the questions asked in it included", () => {
    const chat = room("chat");
    expect(chat.length).toBe(body.room.filter((line) => line.kind === "chat").length);
    expect(chat.every((line) => line.kind === "chat")).toBe(true);
  });

  it("keeps the Q&A's questions, their answers and the chat messages labelled questions", () => {
    const questions = room("questions");
    const expected = body.room.filter((line) => line.kind !== "chat" || line.asks).map((line) => line.id);
    expect(questions.map((line) => line.id).sort()).toEqual([...expected].sort());
    const asked = questions.filter((line) => line.kind === "chat");
    expect(asked.length).toBeGreaterThan(0);
    expect(asked.every((line) => line.asks && line.source === "Chat question")).toBe(true);
  });

  it("keeps everything with both, and on the shared clock too", () => {
    expect(room("all")).toHaveLength(body.room.length);
    const shared = transcriptLines(body, { roomKind: "questions" }).lines.filter((line) => line.kind !== "speech" && line.kind !== "chapter");
    expect(shared.every((line) => line.kind !== "chat" || line.asks)).toBe(true);
  });
});

describe("the room's authors", () => {
  it("are listed by how much they wrote, the unnamed last", () => {
    const authors = authorsOf(body);
    expect(authors.reduce((sum, a) => sum + a.count, 0)).toBe(body.room.length);
    const generic = authors.findIndex((a) => a.name === "Team" || a.name === "Attendee");
    if (generic >= 0) expect(authors.slice(generic).every((a) => a.name === "Team" || a.name === "Attendee")).toBe(true);
    expect(authors[0].count).toBeGreaterThanOrEqual(authors[authors.length - 1].count);
  });

  it("filter the room by one author, and search the room alone", () => {
    const author = authorsOf(body)[0].name;
    const byAuthor = transcriptLines(body, { author });
    expect(byAuthor.lines.length).toBeGreaterThan(0);
    expect(byAuthor.lines.every((line) => line.kind !== "speech" && line.who === author)).toBe(true);
    const apartByAuthor = transcriptLines(apart, { author });
    expect(apartByAuthor.speech).toEqual([], "an author's filter shows their messages");
    const pricing = transcriptLines(apart, { roomQuery: "pricing" });
    expect(pricing.room.length).toBeGreaterThan(0);
    expect(pricing.room.every((line) => /pricing/i.test(line.text) || /pricing/i.test(line.who))).toBe(true);
    expect(pricing.speech.length).toBe(transcriptLines(apart).speech.length, "the chat's search leaves the speech alone");
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

describe("the Transcript tab's chapters", () => {
  const chapterTitles = (lines) => lines.filter((line) => line.kind === "chapter").map((line) => line.title);

  it("head each chapter's first line, on the session clock when interleaved", () => {
    const shown = transcriptLines(withChapters);
    expect(chapterTitles(shown.lines)).toEqual(withChapters.chapters.items.map((c) => c.title));
    expect(shown.counts).toEqual(transcriptLines(body).counts);
    const at = shown.lines.findIndex((line) => line.id === "chapter-U0028");
    expect(shown.lines[at]).toMatchObject({ kind: "chapter", title: "Pricing for larger teams", time: "5:07", timeIsRecording: false });
    // Every line after a heading, up to the next, starts at or after it.
    const next = shown.lines.findIndex((line, i) => i > at && line.kind === "chapter");
    expect(shown.lines.slice(at + 1, next).every((line) => line.t >= 306.9)).toBe(true);
    expect(shown.lines[at + 1].continued).toBeUndefined();
  });

  it("head the speech list on the recording's times when speech is apart", () => {
    // As the server sends them: no session time when speech isn't on the clock.
    const items = withChapters.chapters.items.map((chapter) => ({ ...chapter, t: null }));
    const apartWith = { ...apart, chapters: { state: "ready", items } };
    const shown = transcriptLines(apartWith);
    expect(shown.speech[0]).toMatchObject({ id: "chapter-U0001", time: "0:03", timeIsRecording: true });
    expect(chapterTitles(shown.room)).toEqual([]);
  });

  it("leave out a chapter with no line shown, and one whose line the server didn't send", () => {
    const searched = transcriptLines(withChapters, { query: "pricing works per team" });
    expect(chapterTitles(searched.lines)).toEqual(["Pricing for larger teams"]);
    const ghost = { ...withChapters, chapters: { state: "ready", items: [{ title: "Ghost", first: "U9999", r: 1, t: 1 }] } };
    expect(chapterTitles(transcriptLines(ghost).lines)).toEqual([]);
  });
});
