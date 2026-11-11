// Surprise me, played as "session data scenes": each beat gets a scene built
// from the session's own data, and its caption is revealed word by word in
// step with the beat's clip. Pure functions; the player
// (`components/report/SurprisePlayer.vue`) draws them in SVG and CSS.
//
// Data-honest: a scene shows only a number that is in the beat's own words,
// drawn on the session's real data (the Pulse's audience chat per minute,
// `record.pulse.minutes`). Nothing here computes a figure for the screen.
//
//   curve  a time ("23:00") or a peak word: the audience chat's curve draws
//          in and that minute (or the busiest one) lights up
//   ring   a percentage: a ring fills to it
//   count  any other count above 1: the number counts up, big
//   punch  a punchline: a bold card with a flash of colour
//   words  anything else: the key words highlighted over a soft background

import { busiestMinute, minuteCenter, ridgePaths } from "../report/chartGeometry";
import { countUp } from "./surpriseBeats";

export const SCENES = ["curve", "ring", "count", "punch", "words"];

const CLOCK = /^(?:(\d{1,2}):)?(\d{1,2}):(\d{2})$/;
// Words that say the chat peaked, in English and French.
const PEAK = /\b(?:peak(?:ed|s)?|busiest|spik(?:e|ed|es)|surg(?:e|ed|es)|rush|exploded|erupted|pic)\b/i;

/** "23:00" → 1380, "1:02:05" → 3725 (seconds on the session clock), or null. */
export function clockSeconds(text) {
  const match = CLOCK.exec(String(text ?? "").trim());
  if (!match) return null;
  const [, hours, minutes, seconds] = match;
  if (Number(seconds) > 59 || (hours !== undefined && Number(minutes) > 59)) return null;
  return Number(hours || 0) * 3600 + Number(minutes) * 60 + Number(seconds);
}

function audience(minutes) {
  return (minutes || []).filter((row) => row && Number.isFinite(row.minute));
}

/** The scene for one beat (`{kind, text, parts}`): `{type, …}`.
 * `minutes` are the Pulse's rows (`{minute, audienceChat}`). */
export function sceneFor(beat, { minutes = [] } = {}) {
  const parts = beat?.parts || [];
  const text = String(beat?.text ?? "");
  const numbers = parts.filter((part) => part.kind === "number");
  const rows = audience(minutes);
  const chatty = rows.some((row) => (row.audienceChat || 0) > 0);

  const time = numbers.find((part) => clockSeconds(part.text) !== null);
  if (chatty && (time || PEAK.test(text))) {
    let minute = null;
    let label = null;
    if (time) {
      const at = Math.floor(clockSeconds(time.text) / 60);
      if (rows.some((row) => row.minute === at)) {
        minute = at;
        label = time.text;
      }
    }
    if (minute === null && PEAK.test(text)) minute = busiestMinute(rows, "audienceChat")?.minute ?? null;
    if (minute !== null) return { type: "curve", minute, label, punch: beat.kind === "punch" };
  }

  const percent = numbers.find((part) => /^\d+(?:\.\d+)?%$/.test(part.text));
  if (percent) {
    const value = Number(percent.text.slice(0, -1));
    if (value >= 0 && value <= 100) return { type: "ring", value, label: percent.text, punch: beat.kind === "punch" };
  }

  const count = numbers.find((part) => !part.text.endsWith("%") && countUp(part.text));
  if (count) return { type: "count", label: count.text, punch: beat.kind === "punch" };

  if (beat?.kind === "punch") return { type: "punch", punch: true };
  return { type: "words", title: beat?.kind === "title", punch: false };
}

/** The curve scene's geometry in a `width` × `height` box: the audience
 * chat's line and area (`ridgePaths`), and where `minute` sits on it
 * (`{x, y}`), or null when the minute isn't a Pulse row. */
export function curveGeometry(minutes, minute, { width = 1000, height = 200, top = 16 } = {}) {
  const rows = audience(minutes);
  const values = rows.map((row) => row.audienceChat || 0);
  const { line, area } = ridgePaths(values, { width, height, top });
  const index = rows.findIndex((row) => row.minute === minute);
  if (index < 0 || !line) return null;
  const peak = Math.max(...values);
  const floor = height - 1;
  const x = (minuteCenter(index, rows.length) / 100) * width;
  const y = peak > 0 ? floor - (values[index] / peak) * (floor - top) : floor;
  return { line, area, x: Number(x.toFixed(1)), y: Number(y.toFixed(1)) };
}

/** A beat's caption as words, each with its place in the text: `[{text,
 * kind, start, end}]`, `kind` that of its part (number, keyword, plain); a
 * space stays on the word before it. The words' texts make the beat's text. */
export function captionWords(parts = []) {
  const words = [];
  let at = 0;
  for (const part of parts) {
    const pieces = part.kind === "plain" ? String(part.text).match(/\S+\s*|\s+/g) || [] : [String(part.text)];
    for (const piece of pieces) {
      words.push({ text: piece, kind: part.kind, start: at, end: at + piece.length });
      at += piece.length;
    }
  }
  return words;
}

/** How many characters of a `length`-long caption are shown at `fraction`
 * (0 to 1) of the clip: the words reveal at the speed the clip is read. */
export function revealedChars(length, fraction) {
  const share = Number.isFinite(fraction) ? Math.min(1, Math.max(0, fraction)) : 0;
  return Math.ceil(length * share);
}

/** How long a beat plays without its clip (captions only): the clip's
 * length when the server measured it, else a reading pace. In ms. */
export function beatMs(beat) {
  const seconds = Number(beat?.durationS);
  if (Number.isFinite(seconds) && seconds > 0) return Math.round(seconds * 1000);
  const words = String(beat?.text ?? "").split(/\s+/).filter(Boolean).length;
  return Math.max(1800, words * 380);
}
