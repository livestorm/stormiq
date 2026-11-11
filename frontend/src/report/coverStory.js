// The cover chart's story, minute by minute: what the card shows when someone
// moves across the Report's cover chart (`SessionRidge.vue`). Pure functions
// over what the Report already reads, all without a model call:
//
// - `record.pulse.minutes`: the counts (audience chat, questions);
// - `GET /record/chapters`: the chapter each minute falls in;
// - `GET /record/transcript`: what was being said (only when its clock
//   interleaves speech with the room, `clock.interleave`) and what people
//   wrote, by short name (the server names only who it may);
// - `GET /record/moments`: the key moments, drawn as numbered pins.
//
// The server counts and names; this module picks lines and words them.

import { clockLabel, minuteCenter } from "./chartGeometry";
import { chapterAt, placedChapters } from "./cover";
import { momentView } from "./moments";
import { plural } from "./vitals";

// How much of each line the card shows.
export const SAID_CHARS = 170;
export const LINE_CHARS = 110;
export const MAX_WRITTEN = 3;
export const MAX_ASKED = 2;

const LINK = /https?:\/\//i;

/** `text` cut to `max` characters at a word, with an ellipsis when cut. */
export function clip(text, max) {
  const clean = String(text || "").replace(/\s+/g, " ").trim();
  if (clean.length <= max) return clean;
  const cut = clean.slice(0, max);
  const space = cut.lastIndexOf(" ");
  return `${(space > max * 0.6 ? cut.slice(0, space) : cut).replace(/[\s,.;:–—-]+$/, "")}…`;
}

function finite(value) {
  return typeof value === "number" && Number.isFinite(value);
}

/** The transcript's lines filed by live minute: `{speech, written, asked,
 * spoken}`, each list an array (one entry per minute of `count`) of lines in
 * time order. Speech only when the transcript's clock interleaves it with
 * the room; the team's chat only when nobody else wrote (an internal
 * session, where everyone is the team). Null without a transcript. */
export function storyIndex(transcript, count) {
  if (!transcript || typeof transcript !== "object" || !(count > 0)) return null;
  const empty = () => Array.from({ length: count }, () => []);
  const index = { speech: empty(), written: empty(), asked: empty(), spoken: false };
  const minuteOf = (t) => (finite(t) && t >= 0 ? Math.floor(t / 60) : -1);

  const speakers = new Map((transcript.speakers || []).map((s) => [String(s.key), s.name || ""]));
  if (transcript.clock?.interleave) {
    for (const line of transcript.speech || []) {
      if (!finite(line?.t) || !String(line.text || "").trim()) continue;
      const duration = finite(line.rEnd) && finite(line.r) ? Math.max(line.rEnd - line.r, 0) : 0;
      const first = Math.max(minuteOf(line.t), 0);
      const last = Math.min(minuteOf(line.t + duration), count - 1);
      if (last < 0) continue;
      const entry = { t: line.t, end: line.t + duration, speaker: speakers.get(String(line.speaker)) || "", text: line.text };
      for (let minute = first; minute <= last; minute += 1) index.speech[minute].push(entry);
      index.spoken = true;
    }
  }

  const room = transcript.room || [];
  const audience = room.some((line) => line?.kind === "chat" && !line.team);
  for (const line of room) {
    const minute = minuteOf(line?.t);
    if (minute < 0 || minute >= count || !String(line.text || "").trim()) continue;
    if (line.kind === "question") index.asked[minute].push(line);
    else if (line.kind === "chat" && (!line.team || !audience)) index.written[minute].push(line);
  }
  return index;
}

// What was being said in a minute: the line that covers most of it.
function saidIn(lines, minute) {
  const from = minute * 60;
  const to = from + 60;
  let best = null;
  let most = -1;
  for (const line of lines) {
    const covered = Math.max(Math.min(Math.max(line.end, line.t), to) - Math.max(line.t, from), 0);
    const score = covered + (line.t >= from ? 0.5 : 0);
    if (score > most) {
      best = line;
      most = score;
    }
  }
  return best ? { speaker: best.speaker, text: clip(best.text, SAID_CHARS) } : null;
}

function lineView(line) {
  return { id: line.id, time: clockLabel(line.t), author: line.author || "An attendee", text: clip(line.text, LINE_CHARS) };
}

// A few messages that say something: no links, the longest first, one per
// person while there are more people than places; shown in time order.
function pickWritten(lines) {
  const plain = lines.filter((line) => !LINK.test(line.text));
  const ranked = [...plain].sort((a, b) => b.text.length - a.text.length || a.t - b.t);
  const people = new Set(ranked.map((line) => line.author || line.id)).size;
  const seen = new Set();
  const picked = [];
  for (const line of ranked) {
    const who = line.author || line.id;
    if (seen.has(who) && people >= MAX_WRITTEN) continue;
    seen.add(who);
    picked.push(line);
    if (picked.length === MAX_WRITTEN) break;
  }
  return picked.sort((a, b) => a.t - b.t).map(lineView);
}

/** The key moments as pins on the ridge: `{n, left, top, minute, start,
 * end, label, view}`. `left` is the middle of the moment's busiest minute,
 * `top` the ridge's height there (percent from the top, as `ridgePaths`
 * draws it: the tallest minute 6 below the top, nothing at 99), `view` the
 * moment worded (`momentView`). */
export function momentMarks(momentsBody, minutes) {
  const rows = Array.isArray(minutes) ? minutes : [];
  const count = rows.length;
  if (!count || momentsBody?.state !== "ready" || !Array.isArray(momentsBody.moments)) return [];
  const values = rows.map((row) => (finite(row?.audienceChat) && row.audienceChat > 0 ? row.audienceChat : 0));
  const peak = Math.max(...values);
  return momentsBody.moments
    .filter((moment) => finite(moment?.start) && finite(moment?.end))
    .map((moment) => {
      const first = Math.min(Math.max(Math.floor(moment.start / 60), 0), count - 1);
      const last = Math.min(Math.max(Math.ceil(moment.end / 60) - 1, first), count - 1);
      let busiest = first;
      for (let minute = first; minute <= last; minute += 1) {
        if (values[minute] > values[busiest]) busiest = minute;
      }
      const view = momentView(moment);
      return {
        n: moment.n,
        left: minuteCenter(busiest, count),
        top: peak > 0 ? 99 - (values[busiest] / peak) * 93 : 99,
        minute: busiest,
        start: moment.start,
        end: moment.end,
        label: `Key moment ${moment.n}, ${view.when}: ${view.kind}`,
        view,
      };
    });
}

/** The key moment a minute falls in, or null. */
export function momentAt(marks, minute) {
  const from = minute * 60;
  return marks.find((mark) => mark.start < from + 60 && mark.end > from) || null;
}

/** Everything the card says about minute `index`: `{index, left, time,
 * range, chapter: {title, number, of} | null, counts, quiet, peak, moment,
 * said, written, asked, spoken, text}`. `text` is the card in one line, for
 * the chart's slider (`aria-valuetext`). */
export function minuteStory(index, { minutes, chapters, story, marks = [], peakMinute = null }) {
  const rows = Array.isArray(minutes) ? minutes : [];
  const row = rows[index] || {};
  const minute = finite(row.minute) ? row.minute : index;
  const placed = placedChapters(chapters);
  const chapter = chapterAt(placed, minute * 60 + 30);
  const chat = row.audienceChat || 0;
  const questions = row.questions || 0;

  const counts = [];
  if (chat) counts.push(plural(chat, "message", "messages"));
  if (questions) counts.push(plural(questions, "question", "questions"));

  const said = story?.spoken ? saidIn(story.speech[index] || [], minute) : null;
  const moment = momentAt(marks, minute);
  const result = {
    index,
    left: minuteCenter(index, rows.length),
    time: clockLabel(minute * 60),
    range: `${clockLabel(minute * 60)}–${clockLabel(minute * 60 + 60)}`,
    chapter: chapter ? { title: chapter.title, number: placed.indexOf(chapter) + 1, of: placed.length } : null,
    counts: counts.join(" · "),
    quiet: !chat && !questions,
    peak: peakMinute !== null && minute === peakMinute,
    moment: moment ? { n: moment.n, kind: moment.view.kind } : null,
    said,
    written: story ? pickWritten(story.written[index] || []) : [],
    asked: story ? (story.asked[index] || []).slice(0, MAX_ASKED).map(lineView) : [],
    spoken: Boolean(story?.spoken),
  };

  const parts = [result.time];
  if (result.chapter) parts.push(`chapter “${result.chapter.title}”`);
  parts.push(result.counts || "no messages");
  if (result.peak) parts.push("the busiest minute");
  if (result.moment) parts.push(`key moment ${result.moment.n}: ${result.moment.kind}`);
  if (said) parts.push(`${said.speaker || "Someone"} was saying: ${said.text}`);
  result.text = parts.join(", ");
  return result;
}

/** The minute under a pointer at `x` pixels across a chart `width` wide. */
export function minuteFromX(x, width, count) {
  if (!(width > 0) || !(count > 0) || !finite(x)) return 0;
  return Math.min(Math.max(Math.floor((x / width) * count), 0), count - 1);
}

const STEPS = { ArrowRight: 1, ArrowUp: 1, ArrowLeft: -1, ArrowDown: -1, PageUp: 5, PageDown: -5 };

/** The minute a key moves to from `index` (null or -1: none yet), or null
 * for a key that doesn't move. */
export function minuteFromKey(key, index, count) {
  if (!(count > 0)) return null;
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  const step = STEPS[key];
  if (step === undefined) return null;
  if (index === null || index === undefined || index < 0) return step > 0 ? 0 : count - 1;
  return Math.min(Math.max(index + step, 0), count - 1);
}
