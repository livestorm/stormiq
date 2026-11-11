// Geometry for the v1 Report's time charts: the session clock's labels and
// ticks, and the ridge's path. Pure functions over `record.pulse`; they place
// marks, they never compute a measure.

/** "0:00", "23:40", "1:05:00" from seconds on the session clock; "" when unknown. */
export function clockLabel(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds)) return "";
  const sign = seconds < 0 ? "−" : "";
  const total = Math.round(Math.abs(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const two = (value) => String(value).padStart(2, "0");
  return hours ? `${sign}${hours}:${two(minutes)}:${two(secs)}` : `${sign}${minutes}:${two(secs)}`;
}

const TICK_STEPS = [5, 10, 15, 30, 60];

/** The ticks of a time axis over `count` live minutes: every 5, 10, 15, 30 or
 * 60 minutes, whichever keeps at most `maxTicks`, from 0. Each is `{minute,
 * label, left}`, `left` in percent of the axis (minute 0 at 0%, the end at 100%). */
export function minuteTicks(count, { maxTicks = 10 } = {}) {
  if (!Number.isInteger(count) || count <= 0) return [];
  const step = TICK_STEPS.find((candidate) => Math.floor(count / candidate) + 1 <= maxTicks) ?? TICK_STEPS.at(-1);
  const ticks = [];
  for (let minute = 0; minute <= count; minute += step) {
    ticks.push({ minute, label: clockLabel(minute * 60), left: (minute / count) * 100 });
  }
  return ticks;
}

/** Where minute `index` of `count` sits on a time axis, in percent: the
 * middle of the minute (minute 0 of 30 at 1.7%). */
export function minuteCenter(index, count) {
  if (!(count > 0)) return 0;
  return ((index + 0.5) / count) * 100;
}

/** Where `seconds` on the session clock sits on an axis of `count` minutes,
 * in percent, held between 0 and 100. */
export function secondsLeft(seconds, count) {
  if (!(count > 0) || typeof seconds !== "number" || !Number.isFinite(seconds)) return 0;
  return Math.min(Math.max((seconds / (count * 60)) * 100, 0), 100);
}

/** The ridge: one value per minute (audience chat messages), drawn as a line
 * and the area under it in a `width` × `height` box, the tallest minute
 * `top` units below the top. Each minute's point sits in the middle of its
 * minute (`minuteCenter`), on the same clock as the time axis, and the line
 * runs flat to both edges. Returns `{ line, area }` SVG path data, both
 * empty without values. A chat with no message gives a flat line at the
 * bottom. */
export function ridgePaths(values, { width = 1000, height = 100, top = 6 } = {}) {
  const points = (values || []).map((value) => (typeof value === "number" && Number.isFinite(value) && value > 0 ? value : 0));
  if (!points.length) return { line: "", area: "" };
  const peak = Math.max(...points);
  const floor = height - 1;
  const span = floor - top;
  const x = (index) => (minuteCenter(index, points.length) / 100) * width;
  const y = (value) => (peak > 0 ? floor - (value / peak) * span : floor);
  const coords = points.map((value, index) => `${x(index).toFixed(1)},${y(value).toFixed(1)}`);
  const edge = (at, value) => `${at.toFixed(1)},${y(value).toFixed(1)}`;
  const line = `M${[edge(0, points[0]), ...coords, edge(width, points.at(-1))].join(" L")}`;
  const area = `${line} L${width.toFixed(1)},${height} L0.0,${height} Z`;
  return { line, area };
}

/** A count axis from 0 to at least `max`: integer ticks on a round step (1, 2,
 * 5, 10, 20, 50…), at most `maxTicks` of them. Returns `{ top, ticks }`,
 * `top` the axis's end. An axis with nothing on it still runs 0 to 1. */
export function countTicks(max, { maxTicks = 4 } = {}) {
  const value = typeof max === "number" && Number.isFinite(max) && max > 0 ? Math.ceil(max) : 1;
  let step = 1;
  for (let scale = 1; ; scale *= 10) {
    const found = [1, 2, 5].map((base) => base * scale).find((candidate) => Math.ceil(value / candidate) + 1 <= maxTicks);
    if (found) {
      step = found;
      break;
    }
  }
  const top = Math.ceil(value / step) * step;
  const ticks = [];
  for (let tick = 0; tick <= top; tick += step) ticks.push(tick);
  return { top, ticks };
}

/** A bar's height in percent of an axis that ends at `top`; 0 for a missing
 * or negative value. */
export function barPercent(value, top) {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0 || !(top > 0)) return 0;
  return Math.min((value / top) * 100, 100);
}

/** The busiest minute of one lane, for a chart's spoken description:
 * `{ minute, count }`, the earliest on a tie, or null when every minute is 0. */
export function busiestMinute(minutes, lane) {
  let best = null;
  for (const row of minutes || []) {
    const count = row?.[lane];
    if (typeof count === "number" && count > 0 && (best === null || count > best.count)) {
      best = { minute: row.minute, count };
    }
  }
  return best;
}
