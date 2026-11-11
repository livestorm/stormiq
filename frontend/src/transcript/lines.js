// The Transcript tab's lines, from `GET /api/sessions/{id}/record/transcript`
// (docs/api.md "The v1 record"). Pure functions: they order, filter and word
// what the route sends, and never place speech on the session clock
// themselves: a speech line has a session time only when the server sent one
// (`clock.interleave`).

import { clockLabel } from "../report/chartGeometry";
import { gifParts } from "../utils/gif";

export const VIEWS = [
  { value: "all", label: "Everything" },
  { value: "speech", label: "Speech only" },
  { value: "room", label: "Chat and questions" },
];

// The room's lines to show: everything, the chat only, or the questions
// only (the Q&A's questions and their written answers, and the chat
// messages the session analysis labelled questions, `asks`).
export const ROOM_KINDS = [
  { value: "all", label: "Both" },
  { value: "chat", label: "Chat" },
  { value: "questions", label: "Questions" },
];

const KIND_WORDS = { chat: "Chat", question: "Q&A", answer: "Answer" };

/** Whether a room line (worded) is shown for `roomKind`. */
export function roomKindKeeps(line, roomKind = "all") {
  if (roomKind === "chat") return line.kind === "chat";
  if (roomKind === "questions") return line.kind === "question" || line.kind === "answer" || line.asks;
  return true;
}

// Speakers take a colour each, in order of first appearance, from this many
// (`--siq-speaker-1` … in tokens.css); a seventh speaker reuses the first.
export const SPEAKER_TONES = 6;

function text(value) {
  return String(value ?? "").trim();
}

function speakerNames(body) {
  const names = new Map();
  for (const speaker of body?.speakers || []) names.set(speaker.key ?? null, text(speaker.name) || "Speaker");
  return names;
}

/** Each speaker's colour, 1 to `SPEAKER_TONES`, in the server's order. */
function speakerTones(body) {
  const tones = new Map();
  (body?.speakers || []).forEach((speaker, index) => tones.set(speaker.key ?? null, (index % SPEAKER_TONES) + 1));
  return tones;
}

/** A speech line, worded: `{id, kind: "speech", t, time, who, tone, text,
 * parts}`. `time` is the session time when the server placed it, else the
 * recording time, marked as such (`timeIsRecording`); `tone` is the
 * speaker's colour. */
function speechLine(line, names, tones) {
  const onClock = typeof line.t === "number";
  return {
    id: line.id,
    kind: "speech",
    t: onClock ? line.t : null,
    time: onClock ? clockLabel(line.t) : clockLabel(line.r),
    timeIsRecording: !onClock,
    who: names.get(line.speaker ?? null) || "Speaker",
    tone: tones.get(line.speaker ?? null) || 1,
    speaker: line.speaker ?? null,
    text: line.text,
    parts: textParts(line.text, line.unclear),
  };
}

/** A chat, Q&A or answer line, worded: `{id, kind, t, time, who, source,
 * text, status, team, asks, gif}`; `asks` marks a chat message the
 * session analysis labelled a question ("Chat question"). A time before the session's start reads
 * "Before"; `gif` is the GIF's URL when a chat message is only a GIF link
 * (`utils/gif.js`), else "". */
function roomLine(line) {
  const status =
    line.kind === "question" ? (line.answered ? "Answered in writing" : "") : line.kind === "answer" ? "Written answer" : "";
  return {
    id: line.id,
    kind: line.kind,
    t: typeof line.t === "number" ? line.t : null,
    time: typeof line.t === "number" ? (line.t < 0 ? "Before" : clockLabel(line.t)) : "",
    timeIsRecording: false,
    who: text(line.author) || "Attendee",
    source: line.kind === "chat" && line.asks ? "Chat question" : KIND_WORDS[line.kind] || "Chat",
    text: line.text,
    // A GIF's link is shown as the GIF, under the message's other words.
    parts: [{ text: line.kind === "chat" ? gifParts(line.text).text : line.text, unclear: false }],
    status,
    team: Boolean(line.team),
    asks: line.kind === "chat" && Boolean(line.asks),
    gif: line.kind === "chat" ? gifParts(line.text).gif : "",
  };
}

/** `text` cut into `[{text, unclear}]` at the server's unclear spans. Spans
 * that overlap, run backwards or fall outside the text are ignored; unclear
 * words with only spaces between them make one part (one "low confidence"
 * for a screen reader, not one per word). */
export function textParts(value, spans = []) {
  const source = String(value ?? "");
  const parts = [];
  let cursor = 0;
  for (const [start, end] of [...(spans || [])].sort((a, b) => a[0] - b[0])) {
    if (!(Number.isInteger(start) && Number.isInteger(end)) || start < cursor || end <= start || end > source.length) continue;
    const gap = source.slice(cursor, start);
    const last = parts[parts.length - 1];
    if (last?.unclear && !gap.trim()) {
      last.text += gap + source.slice(start, end);
    } else {
      if (gap) parts.push({ text: gap, unclear: false });
      parts.push({ text: source.slice(start, end), unclear: true });
    }
    cursor = end;
  }
  if (cursor < source.length || !parts.length) parts.push({ text: source.slice(cursor), unclear: false });
  return parts;
}

function matches(line, needle) {
  if (!needle) return true;
  return [line.text, line.who].some((value) => text(value).toLowerCase().includes(needle));
}

/** The chapters the server sent, in order, each with the index of its
 * first speech line (`first`, an utterance id) in the server's speech
 * order. A chapter whose first line the server didn't send is left out. */
function chapterStarts(body) {
  const index = new Map((body?.speech || []).map((line, i) => [line.id, i]));
  return (body?.chapters?.items || [])
    .filter((chapter) => index.has(chapter.first) && text(chapter.title))
    .map((chapter) => ({ ...chapter, at: index.get(chapter.first) }))
    .sort((a, b) => a.at - b.at);
}

/** A chapter heading line: `{id, kind: "chapter", title, time,
 * timeIsRecording}`, on the session clock when the server placed it there. */
function chapterLine(chapter) {
  const onClock = typeof chapter.t === "number";
  return {
    id: `chapter-${chapter.first}`,
    kind: "chapter",
    title: text(chapter.title),
    time: onClock ? clockLabel(chapter.t) : typeof chapter.r === "number" ? clockLabel(chapter.r) : "",
    timeIsRecording: !onClock,
  };
}

/** `lines` with a chapter heading before the first shown line of each
 * chapter. A speech line belongs to the last chapter starting at or before
 * it (by the server's speech order); an audience line, only in the
 * interleaved list, to the last chapter starting at or before its session
 * time. Lines before the first chapter get no heading, and a chapter with
 * no line shown (a search, a filter) none either. */
function withChapters(lines, body, speechOrder, { byTime }) {
  const chapters = chapterStarts(body);
  if (!chapters.length) return lines;
  const chapterOf = (line) => {
    let found = -1;
    chapters.forEach((chapter, i) => {
      if (line.kind === "speech" ? chapter.at <= speechOrder.get(line.id) : byTime && chapter.t != null && line.t != null && chapter.t <= line.t) {
        found = i;
      }
    });
    return found;
  };
  const out = [];
  let current = -1;
  for (const line of lines) {
    const index = chapterOf(line);
    if (index > current) {
      out.push(chapterLine(chapters[index]));
      current = index;
    }
    out.push(line);
  }
  return out;
}

/** The room's authors, most messages first (then by name): `[{name,
 * count}]`, for the author filter. "Team" and "Attendee" (authors the
 * people list couldn't name) are kept, last. */
export function authorsOf(body) {
  const counts = new Map();
  for (const line of body?.room || []) {
    const who = text(line.author) || "Attendee";
    counts.set(who, (counts.get(who) || 0) + 1);
  }
  const generic = (name) => (name === "Team" || name === "Attendee" ? 1 : 0);
  return [...counts]
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => generic(a.name) - generic(b.name) || b.count - a.count || a.name.localeCompare(b.name));
}

/** What the page shows: `{interleaved, lines, speech, room, counts}`.
 *
 * Chapter headings (`kind: "chapter"`, from the server's `chapters`) sit
 * among the speech lines, and among all lines when interleaved; `counts`
 * leaves them out.
 *
 * - Interleaved (the server placed the speech on the session clock): one
 *   list, `lines`, in session time; a speech line comes before an audience
 *   line at the same second.
 * - Otherwise `speech` (in recording order) and `room` (in session time)
 *   apart, and `lines` is empty.
 *
 * `view` keeps speech, the room or both; `query` keeps lines whose text or
 * author has it (case-insensitive); `speaker` keeps one speaker's lines (the
 * room's lines go when a speaker is picked); `author` keeps one author's
 * room lines (speech goes when an author is picked); `roomQuery` searches
 * the room's lines only (the chat column's own search); `roomKind` keeps
 * the room's chat, its questions or both (`ROOM_KINDS`, `roomKindKeeps`). */
export function transcriptLines(
  body,
  { view = "all", query = "", speaker = undefined, author = undefined, roomQuery = "", roomKind = "all" } = {},
) {
  const names = speakerNames(body);
  const tones = speakerTones(body);
  const needle = text(query).toLowerCase();
  const roomNeedle = text(roomQuery).toLowerCase();
  const wantSpeech = view !== "room" && author === undefined;
  const wantRoom = view !== "speech" && speaker === undefined;
  const speech = wantSpeech
    ? (body?.speech || [])
        .map((line) => speechLine(line, names, tones))
        .filter((line) => speaker === undefined || line.speaker === speaker)
        .filter((line) => matches(line, needle))
    : [];
  const room = wantRoom
    ? (body?.room || [])
        .map(roomLine)
        .filter((line) => author === undefined || line.who === author)
        .filter((line) => roomKindKeeps(line, roomKind))
        .filter((line) => matches(line, needle) && matches(line, roomNeedle))
    : [];
  const interleaved = Boolean(body?.clock?.interleave);
  const counts = { speech: speech.length, room: room.length };
  const speechOrder = new Map((body?.speech || []).map((line, i) => [line.id, i]));
  if (!interleaved) {
    return { interleaved, lines: [], speech: continued(withChapters(speech, body, speechOrder, { byTime: false })), room, counts };
  }
  const order = (line) => (line.t == null ? Number.POSITIVE_INFINITY : line.t);
  const lines = [...speech, ...room]
    .map((line, index) => ({ line, index, speechFirst: line.kind === "speech" ? 0 : 1 }))
    .sort((a, b) => order(a.line) - order(b.line) || a.speechFirst - b.speechFirst || a.index - b.index)
    .map((entry) => entry.line);
  return { interleaved, lines: continued(withChapters(lines, body, speechOrder, { byTime: true })), speech: [], room: [], counts };
}

/** Marks a speech line that follows the same speaker's (`continued`): the
 * page then shows the name once per stretch. */
function continued(lines) {
  return lines.map((line, index) => {
    const before = lines[index - 1];
    const same = line.kind === "speech" && before?.kind === "speech" && before.speaker === line.speaker;
    return same ? { ...line, continued: true } : line;
  });
}

function srtTime(seconds) {
  const total = Math.max(0, Math.round(seconds * 1000));
  const ms = total % 1000;
  const s = Math.floor(total / 1000) % 60;
  const m = Math.floor(total / 60000) % 60;
  const h = Math.floor(total / 3600000);
  const two = (value) => String(value).padStart(2, "0");
  return `${two(h)}:${two(m)}:${two(s)},${String(ms).padStart(3, "0")}`;
}

/** Subtitles for the recording (SubRip): one cue per speech line, on the
 * recording's own times, "Name: text". Lines without a time are left out;
 * a cue without an end lasts until the next one starts, or 3 s. */
export function subtitles(body) {
  const names = speakerNames(body);
  const timed = (body?.speech || []).filter((line) => typeof line.r === "number");
  return timed
    .map((line, index) => {
      const next = timed[index + 1];
      const end = typeof line.rEnd === "number" && line.rEnd > line.r ? line.rEnd : next ? next.r : line.r + 3;
      const who = names.get(line.speaker ?? null) || "Speaker";
      return `${index + 1}\n${srtTime(line.r)} --> ${srtTime(end)}\n${who}: ${String(line.text).replace(/\s*\n\s*/g, " ")}\n`;
    })
    .join("\n");
}

/** The speakers to name and filter by, in order: `{key, name, named,
 * tone}` (`tone`: their colour, as on their lines). */
export function speakersOf(body) {
  return (body?.speakers || []).map((speaker, index) => ({
    key: speaker.key ?? null,
    name: text(speaker.name),
    named: Boolean(speaker.named),
    tone: (index % SPEAKER_TONES) + 1,
  }));
}
