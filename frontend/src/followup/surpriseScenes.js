// Surprise me, played as a storyboard: each beat gets one scene that tells
// the story while the voice reads the sentence. The screen never writes the
// sentence out (captions are an option, off by default): it shows one short
// headline lifted word for word from the beat, a big number the beat says,
// or one of the room's own lines popping up as a chat bubble, with an effect.
// Pure functions; the player (`components/report/SurprisePlayer.vue`) draws
// them in SVG and CSS.
//
// Where a scene comes from (`storyScene`):
// - the server's storyboard (`beats[i].scene` from `GET /surprise-voice`):
//   made by Claude on the Play click and checked word for word on the server
//   (`livestorm_app/ai/surprise_story.py`); its quote's words are the room's
//   own, redacted, never the model's;
// - else, or when a beat's scene is missing, the page's own rules
//   (`ruleScene`): the beat's key words, a number it says, the Pulse for a
//   time or a peak.
//
// Data-honest: a scene shows only words and numbers that are in the beat
// (or the room's own line), drawn on the session's real data (the Pulse's
// audience chat per minute, `record.pulse.minutes`). Nothing here computes a
// figure for the screen.
//
//   slam   the headline slams in, big (the title)
//   stamp  the headline is stamped on at an angle (a dry punchline)
//   pop    the headline's words pop in one by one
//   count  the number counts up, big; a percentage fills a ring
//   chat   the room's line flies in as a chat bubble
//   pulse  the audience chat's curve draws in, its minute lights up
//   flash  a flash of colour behind the headline (a punchline)

import { busiestMinute, minuteCenter, ridgePaths } from "../report/chartGeometry";
import { countUp } from "./surpriseBeats";

export const EFFECTS = ["slam", "stamp", "pop", "count", "chat", "pulse", "flash"];
export const MAX_HEADLINE_WORDS = 6;

const CLOCK = /^(?:(\d{1,2}):)?(\d{1,2}):(\d{2})$/;
const PERCENT = /^(\d+(?:\.\d+)?)%$/;
// Words that say the chat peaked, in English and French.
const PEAK = /\b(?:peak(?:ed|s)?|busiest|spik(?:e|ed|es)|surg(?:e|ed|es)|rush|exploded|erupted|pic)\b/i;
const EDGE = /^[("“‘'«[]+|[)"”’'»\].,!?;:…]+$/g;

// Filler words: a headline window scores nothing for them.
const STOPWORDS = new Set(
  `a an the and or but so of to in on at for by with from as is are was were be been it its it's this that these those
  then than there here who what when where which while just very also too not no yes we you they he she i me my our your
  their them us his her le la les un une des et ou de du en au aux est sont il elle ils elles on nous vous ce cette`.split(/\s+/),
);

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

function words(text) {
  return String(text ?? "").split(/\s+/).filter(Boolean);
}

function trimEdges(text) {
  return String(text).replace(/^[("“‘'«]+/, "").replace(/[)"”’'».,!?;:…]+$/, "").trim();
}

/** A headline's words, for the word-by-word pop: `["Fifty", "messages"]`. */
export function headlineWords(headline) {
  return words(headline);
}

/** The page's own headline for a beat: at most `MAX_HEADLINE_WORDS` words in
 * a row of its sentence (the whole sentence when it is that short), else the
 * five-word window that holds the most of what matters (numbers and key
 * words, then long words, never filler at its edges), the last best one for
 * a punchline, which lands at the end. Always the beat's own words. */
export function ruleHeadline(beat) {
  const parts = beat?.parts?.length ? beat.parts : [{ kind: "plain", text: String(beat?.text ?? "") }];
  const tokens = [];
  for (const part of parts) {
    for (const word of part.kind === "plain" ? words(part.text) : [String(part.text)]) {
      const plain = word.replace(EDGE, "");
      if (!plain) continue;
      const score = part.kind !== "plain" ? 3 : STOPWORDS.has(plain.toLowerCase()) ? 0 : plain.length >= 5 ? 1.5 : 1;
      tokens.push({ word, score });
    }
  }
  if (tokens.length <= MAX_HEADLINE_WORDS) return trimEdges(tokens.map((token) => token.word).join(" "));
  const size = MAX_HEADLINE_WORDS - 1;
  const late = beat?.kind === "punch";
  let best = 0;
  let bestScore = -Infinity;
  for (let start = 0; start + size <= tokens.length; start += 1) {
    const window = tokens.slice(start, start + size);
    const edges = (window[0].score === 0 ? -0.5 : 0) + (window[size - 1].score === 0 ? -0.5 : 0);
    const score = window.reduce((sum, token) => sum + token.score, 0) + edges;
    if (score > bestScore || (late && score === bestScore)) {
      best = start;
      bestScore = score;
    }
  }
  return trimEdges(tokens.slice(best, best + size).map((token) => token.word).join(" "));
}

/** A ring's value for a percentage ("56%" → 56), or null. */
export function percentValue(number) {
  const match = PERCENT.exec(String(number ?? ""));
  if (!match) return null;
  const value = Number(match[1]);
  return value >= 0 && value <= 100 ? value : null;
}

/** The Pulse's minute for a beat: the minute of a time it gives, else the
 * busiest minute when it says the chat peaked (or `peak` is set);
 * `{minute, label}`, or null when the session has no chat curve. */
function pulseMinute(beat, rows, peak = false) {
  if (!rows.some((row) => (row.audienceChat || 0) > 0)) return null;
  const time = (beat?.parts || []).find((part) => part.kind === "number" && clockSeconds(part.text) !== null);
  if (time) {
    const at = Math.floor(clockSeconds(time.text) / 60);
    if (rows.some((row) => row.minute === at)) return { minute: at, label: time.text };
  }
  if (peak || PEAK.test(String(beat?.text ?? ""))) {
    const minute = busiestMinute(rows, "audienceChat")?.minute;
    if (Number.isFinite(minute)) return { minute, label: null };
  }
  return null;
}

/** The page's own scene for a beat (`{kind, text, parts}`), when the server
 * sent none: `{effect, headline, number, quote, minute, label, punch}`.
 * `index` alternates a punchline's flash and stamp. */
export function ruleScene(beat, { minutes = [], index = 0 } = {}) {
  const punch = beat?.kind === "punch";
  const base = { headline: ruleHeadline(beat), number: null, quote: null, minute: null, label: null, punch };
  const pulse = pulseMinute(beat, audience(minutes));
  if (pulse) return { ...base, effect: "pulse", ...pulse };
  const count = (beat?.parts || []).find((part) => part.kind === "number" && (percentValue(part.text) !== null || countUp(part.text)));
  if (count) return { ...base, effect: "count", number: count.text };
  if (beat?.kind === "title") return { ...base, effect: "slam" };
  if (punch) return { ...base, effect: index % 2 ? "stamp" : "flash" };
  return { ...base, effect: "pop" };
}

function serverScene(scene) {
  if (!scene || typeof scene !== "object" || !EFFECTS.includes(scene.effect)) return null;
  const headline = typeof scene.headline === "string" ? scene.headline.trim() : "";
  const given = scene.quote && typeof scene.quote === "object" ? scene.quote : null;
  const quote =
    given && typeof given.text === "string" && given.text.trim()
      ? { kind: given.kind === "question" ? "question" : "chat", t: Number.isFinite(given.t) ? given.t : null, text: given.text }
      : null;
  const number = typeof scene.number === "string" && scene.number ? scene.number : null;
  if (scene.effect === "chat" && !quote) return null;
  if (scene.effect === "count" && !number) return null;
  if (!["chat", "count"].includes(scene.effect) && !headline) return null;
  return { effect: scene.effect, headline, number: scene.effect === "count" ? number : null, quote: scene.effect === "chat" ? quote : null };
}

/** The scene the player shows for a beat: the server's checked one when it
 * sent it (`beat.scene`), else the page's own (`ruleScene`). A pulse needs
 * the session's chat curve: without one its headline slams in instead. */
export function storyScene(beat, { minutes = [], index = 0 } = {}) {
  const given = serverScene(beat?.scene);
  if (!given) return ruleScene(beat, { minutes, index });
  const scene = { ...given, minute: null, label: null, punch: beat?.kind === "punch" };
  if (scene.effect !== "pulse") return scene;
  const pulse = pulseMinute(beat, audience(minutes), true);
  return pulse ? { ...scene, ...pulse } : { ...scene, effect: "slam" };
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

/** "23:05" for a time on the session clock in seconds, or "". */
export function clockLabel(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "";
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const mins = Math.floor((total % 3600) / 60);
  const secs = String(total % 60).padStart(2, "0");
  return hours ? `${hours}:${String(mins).padStart(2, "0")}:${secs}` : `${mins}:${secs}`;
}

/** How long a beat plays without its clip (sound off): the clip's length
 * when the server measured it, else a reading pace. In ms. */
export function beatMs(beat) {
  const seconds = Number(beat?.durationS);
  if (Number.isFinite(seconds) && seconds > 0) return Math.round(seconds * 1000);
  return Math.max(1800, words(beat?.text).length * 380);
}
