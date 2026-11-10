// The Transcript tab's lines, from `GET /api/sessions/{id}/record/transcript`
// (docs/api.md "The v1 record"). Pure functions: they order, filter and word
// what the route sends, and never place speech on the session clock
// themselves: a speech line has a session time only when the server sent one
// (`clock.interleave`).

import { clockLabel } from "../report/chartGeometry";

export const VIEWS = [
  { value: "all", label: "Everything" },
  { value: "speech", label: "Speech only" },
  { value: "room", label: "Chat and questions" },
];

const KIND_WORDS = { chat: "Chat", question: "Q&A", answer: "Answer" };

function text(value) {
  return String(value ?? "").trim();
}

function speakerNames(body) {
  const names = new Map();
  for (const speaker of body?.speakers || []) names.set(speaker.key ?? null, text(speaker.name) || "Speaker");
  return names;
}

/** A speech line, worded: `{id, kind: "speech", t, time, who, text, parts}`.
 * `time` is the session time when the server placed it, else the recording
 * time, marked as such (`timeIsRecording`). */
function speechLine(line, names) {
  const onClock = typeof line.t === "number";
  return {
    id: line.id,
    kind: "speech",
    t: onClock ? line.t : null,
    time: onClock ? clockLabel(line.t) : clockLabel(line.r),
    timeIsRecording: !onClock,
    who: names.get(line.speaker ?? null) || "Speaker",
    speaker: line.speaker ?? null,
    text: line.text,
    parts: textParts(line.text, line.unclear),
  };
}

/** A chat, Q&A or answer line, worded: `{id, kind, t, time, who, source,
 * text, status, team}`. A time before the session's start reads "Before". */
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
    source: KIND_WORDS[line.kind] || "Chat",
    text: line.text,
    parts: [{ text: line.text, unclear: false }],
    status,
    team: Boolean(line.team),
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

/** What the page shows: `{interleaved, lines, speech, room, counts}`.
 *
 * - Interleaved (the server placed the speech on the session clock): one
 *   list, `lines`, in session time; a speech line comes before an audience
 *   line at the same second.
 * - Otherwise `speech` (in recording order) and `room` (in session time)
 *   apart, and `lines` is empty.
 *
 * `view` keeps speech, the room or both; `query` keeps lines whose text or
 * author has it (case-insensitive); `speaker` keeps one speaker's lines (the
 * room's lines go when a speaker is picked). */
export function transcriptLines(body, { view = "all", query = "", speaker = undefined } = {}) {
  const names = speakerNames(body);
  const needle = text(query).toLowerCase();
  const wantSpeech = view !== "room";
  const wantRoom = view !== "speech" && speaker === undefined;
  const speech = wantSpeech
    ? (body?.speech || [])
        .map((line) => speechLine(line, names))
        .filter((line) => speaker === undefined || line.speaker === speaker)
        .filter((line) => matches(line, needle))
    : [];
  const room = wantRoom ? (body?.room || []).map(roomLine).filter((line) => matches(line, needle)) : [];
  const interleaved = Boolean(body?.clock?.interleave);
  const counts = { speech: speech.length, room: room.length };
  if (!interleaved) return { interleaved, lines: [], speech: continued(speech), room, counts };
  const order = (line) => (line.t == null ? Number.POSITIVE_INFINITY : line.t);
  const lines = [...speech, ...room]
    .map((line, index) => ({ line, index, speechFirst: line.kind === "speech" ? 0 : 1 }))
    .sort((a, b) => order(a.line) - order(b.line) || a.speechFirst - b.speechFirst || a.index - b.index)
    .map((entry) => entry.line);
  return { interleaved, lines: continued(lines), speech: [], room: [], counts };
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

/** The speakers to name, in order: `{key, name, named}`. */
export function speakersOf(body) {
  return (body?.speakers || []).map((speaker) => ({
    key: speaker.key ?? null,
    name: text(speaker.name),
    named: Boolean(speaker.named),
  }));
}
