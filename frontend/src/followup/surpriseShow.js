// Surprise me's show, played: the timeline the player walks through. Pure
// functions over the server's show (`GET /surprise-show`) and the take's
// beats; no model call, no audio.
//
// - A unit is one line of the performance script, on its beat (`line.beat`,
//   the beat's index), read by its own clip. The screen shows the beat's
//   scene for as long as its lines are said; CC shows the beat's sentence
//   (the take's own words: the script is only ever heard).
// - Before the show is recorded there are no lines: one unit per beat, for
//   "Play without sound".
// - Gaps between clips, as the rehearsal set them: `LINE_GAP_MS` between two
//   lines of one beat, `BEAT_GAP_MS` when the beat changes.

import { beatMs } from "./surpriseScenes";

export const LINE_GAP_MS = 220;
export const BEAT_GAP_MS = 380;
export const MIN_SILENT_LINE_MS = 900;

/** The units: `[{beat, clip, durationS, ready}]`, one per script line that
 * fits the beats, else one per beat. */
export function showUnits(body, beats = []) {
  const lines = (Array.isArray(body?.lines) ? body.lines : []).filter(
    (line) => Number.isInteger(line?.beat) && line.beat >= 0 && line.beat < beats.length,
  );
  if (lines.length) {
    return lines.map((line) => ({
      beat: line.beat,
      clip: String(line.clip || ""),
      durationS: Number(line.durationS) > 0 ? Number(line.durationS) : null,
      ready: Boolean(line.ready),
    }));
  }
  return beats.map((_, beat) => ({ beat, clip: "", durationS: null, ready: false }));
}

/** The silence after unit `i` before the next one starts (0 after the last). */
export function gapAfter(units, i) {
  const next = units[i + 1];
  if (!next) return 0;
  return next.beat === units[i]?.beat ? LINE_GAP_MS : BEAT_GAP_MS;
}

/** How long unit `i` plays without sound: its clip's length, else its share
 * of its beat's reading time. */
export function unitMs(units, i, beats = []) {
  const unit = units[i];
  if (!unit) return 0;
  if (unit.durationS) return unit.durationS * 1000;
  const share = units.filter((other) => other.beat === unit.beat).length || 1;
  return Math.max(MIN_SILENT_LINE_MS, beatMs(beats[unit.beat]) / share);
}

/** How long a beat is on screen: its units and the gaps between them. */
export function beatSpanMs(units, beat, beats = []) {
  let total = 0;
  units.forEach((unit, i) => {
    if (unit.beat !== beat) return;
    total += unitMs(units, i, beats);
    if (units[i + 1]?.beat === beat) total += LINE_GAP_MS;
  });
  return total;
}

/** The first unit of a beat, or -1. */
export function firstUnitOf(units, beat) {
  return units.findIndex((unit) => unit.beat === beat);
}

/** How full a beat's segment is (0 to 1) while unit `index` plays at
 * `fraction` of its length. */
export function beatFill(units, index, fraction, beat) {
  const current = units[index];
  if (!current) return 0;
  if (beat < current.beat) return 1;
  if (beat > current.beat) return 0;
  const mine = [];
  units.forEach((unit, i) => unit.beat === beat && mine.push(i));
  return Math.min(1, (mine.indexOf(index) + Math.min(1, Math.max(0, fraction))) / mine.length);
}
