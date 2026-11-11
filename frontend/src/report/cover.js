// The Report's cover chart: what it marks and what it says at a glance.
// Pure functions over `record.pulse.minutes` and `GET /record/chapters`. They
// place marks and pick the largest of the values the server sent; they never
// compute a measure (the chapters' counts and rates come from the server:
// `record.assemble.chapter_marks`).

import { busiestMinute, clockLabel, minuteCenter, secondsLeft } from "./chartGeometry";

const plural = (count, one, many) => `${count} ${count === 1 ? one : many}`;

/** The chapters the cover can draw: the server placed them on the session
 * clock (`placed`), in order, each with a `t`. Anything else draws none. */
export function placedChapters(body) {
  if (!body || body.state !== "ready" || !body.placed || !Array.isArray(body.chapters)) return [];
  return body.chapters.filter((chapter) => typeof chapter?.t === "number" && Number.isFinite(chapter.t));
}

/** The chapter band: each chapter from its start to the next one's (the last
 * to the end of the axis), in percent of an axis of `count` minutes. */
export function chapterSegments(chapters, count) {
  return chapters.map((chapter, index) => {
    const left = secondsLeft(chapter.t, count);
    const next = chapters[index + 1];
    const right = next ? secondsLeft(next.t, count) : 100;
    return {
      key: `${index}:${chapter.title}`,
      title: chapter.title,
      left,
      width: Math.max(right - left, 0),
      start: clockLabel(chapter.t),
    };
  });
}

/** The chapter a time falls in: the last one starting at or before it. */
export function chapterAt(chapters, seconds) {
  let found = null;
  for (const chapter of chapters) {
    if (chapter.t <= seconds) found = chapter;
  }
  return found;
}

// The chapter with the most of `field`, only when it leads alone: null when
// every chapter has none, or two share the most (a tie says nothing).
function mostOf(chapters, field) {
  let best = null;
  let tied = false;
  for (const chapter of chapters) {
    const value = chapter[field];
    if (typeof value !== "number" || value <= 0) continue;
    if (best === null || value > best[field]) {
      best = chapter;
      tied = false;
    } else if (value === best[field]) {
      tied = true;
    }
  }
  return tied ? null : best;
}

/** Everything the cover draws and says, for `minutes` (`record.pulse.minutes`)
 * and the chapters' response (`GET /record/chapters`, or null):
 *
 * - `peak`: the busiest minute of the audience's chat, `{left, count, time,
 *   chapter}`, or null when nobody wrote;
 * - `questions`: a mark per minute with questions, `{left, count, title}`;
 * - `segments`: the chapter band (`chapterSegments`), and `lively`, the key
 *   of the chapter with the most chat per minute (only with two or more);
 * - `glance`: up to three `{label, value, detail}` lines under the chart;
 * - `description`: the chart in one sentence, for screen readers. */
export function coverModel(minutes, chaptersBody) {
  const rows = Array.isArray(minutes) ? minutes : [];
  const count = rows.length;
  const chapters = placedChapters(chaptersBody);
  const segments = chapterSegments(chapters, count);

  const busiest = busiestMinute(rows, "audienceChat");
  const peak = busiest
    ? {
        left: minuteCenter(rows.findIndex((row) => row.minute === busiest.minute), count),
        count: busiest.count,
        time: clockLabel(busiest.minute * 60),
        chapter: chapterAt(chapters, busiest.minute * 60 + 30)?.title || "",
      }
    : null;

  const questions = rows
    .map((row, index) => ({ row, index }))
    .filter(({ row }) => (row?.questions || 0) > 0)
    .map(({ row, index }) => ({
      left: minuteCenter(index, count),
      count: row.questions,
      title: `${plural(row.questions, "question", "questions")} at ${clockLabel(row.minute * 60)}`,
    }));

  const lively = chapters.length >= 2 ? mostOf(chapters, "chatPerMinute") : null;
  const asked = chapters.length >= 2 ? mostOf(chapters, "questions") : null;
  const livelyIndex = lively ? chapters.indexOf(lively) : -1;

  const glance = [];
  if (peak) {
    glance.push({
      label: "Busiest minute",
      value: peak.time,
      detail: `${plural(peak.count, "message", "messages")}${peak.chapter ? `, during “${peak.chapter}”` : ""}`,
    });
  }
  if (lively) {
    glance.push({
      label: "Liveliest chapter",
      value: lively.title,
      detail: `${lively.chatPerMinute} ${lively.chatPerMinute === 1 ? "message" : "messages"} a minute`,
    });
  }
  if (asked) {
    glance.push({ label: "Most questions", value: asked.title, detail: plural(asked.questions, "question", "questions") });
  }

  const parts = [`Audience chat messages per minute over ${plural(count, "minute", "minutes")}`];
  if (peak) parts.push(`busiest at ${peak.time} with ${plural(peak.count, "message", "messages")}${peak.chapter ? `, during “${peak.chapter}”` : ""}`);
  else parts.push("nobody in the audience wrote in the chat");
  if (questions.length) parts.push(`questions were asked in ${plural(questions.length, "minute", "minutes")}`);
  if (segments.length) parts.push(`${plural(segments.length, "chapter", "chapters")}: ${segments.map((s) => `${s.title} from ${s.start}`).join(", ")}`);

  return {
    count,
    peak,
    questions,
    segments,
    lively: livelyIndex >= 0 ? segments[livelyIndex].key : "",
    glance,
    description: `${parts.join("; ")}.`,
  };
}
