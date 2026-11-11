// The words of the Report's findings (`GET /record/findings`,
// `record/findings.py`): every sentence comes from a template here, filled
// with the server's values. Nothing is computed but a time label and a
// multiple of 100 for the index.

import { clockLabel } from "./chartGeometry";

const plural = (count, one, many) => `${count} ${count === 1 ? one : many}`;
const minutes = (seconds) => Math.max(1, Math.round(seconds / 60));
const times = (index) => `${(index / 100).toFixed(1).replace(/\.0$/, "")}×`;

// ── Found ──

function chaptersFinding(values) {
  const rows = values.chapters || [];
  const high = rows.find((row) => row.title === values.high);
  const low = rows.find((row) => row.title === values.low);
  const headline = high ? `“${high.title}” drew the room in` : `The room went quiet during “${low.title}”`;
  const lines = [];
  if (high) {
    lines.push(
      `${high.perMinute} unprompted chat messages a minute from ${plural(high.chatters, "person", "people")}, ${times(high.index)} the session's usual chapter.`,
    );
  }
  if (low) {
    lines.push(`During “${low.title}” the chat fell to ${low.perMinute} a minute, ${times(low.index)} the usual.`);
  }
  lines.push("Greetings, thanks, logistics and replies to a question from the stage don't count: only what the room said on its own.");
  const bars = rows.map((row) => ({
    title: row.title,
    start: clockLabel(row.t),
    length: `${minutes(row.seconds)} min`,
    index: row.index,
    value: row.index === null ? (row.qa ? plural(row.questions, "question", "questions") : "too short") : String(row.index),
    tone: row.title === values.high ? "high" : row.title === values.low ? "low" : "",
  }));
  return { headline, lines, bars, rows: [] };
}

function frictionFinding(values) {
  const found = values.incidents || [];
  const late = values.lateStart;
  const top = found.reduce((best, item) => (best === null || item.messages > best.messages ? item : best), null);
  const headline = top
    ? `${plural(top.people, "person", "people")} flagged a sound or connection problem at ${clockLabel(top.start)}`
    : `The session started ${minutes(late.lateS)} minutes late`;
  const rows = found.map((item) => {
    const parts = [`${plural(item.messages, "message", "messages")} from ${plural(item.people, "person", "people")}`];
    if (values.speech) parts.push(item.deadAirS > 0 ? `${Math.round(item.deadAirS)} s of silence on stage` : "no silence on stage");
    return `${clockLabel(item.start)}–${clockLabel(item.end)} · ${parts.join(" · ")}`;
  });
  if (late && top) rows.push(`Started ${minutes(late.lateS)} min late · ${plural(late.waitingMessages, "message", "messages")} in the waiting room`);
  const lines = top
    ? ["Messages the analysis labelled as sound, video or connection issues, grouped when they came within two minutes of each other."]
    : [`${plural(late.waitingMessages, "message", "messages")} from the audience in the waiting room before it started.`];
  return { headline, lines, bars: [], rows };
}

const FOUND = { chapters: chaptersFinding, friction: frictionFinding };

/** A found finding in words: `{id, headline, lines, bars, rows, confidence}`. */
export function foundFinding(finding) {
  const words = FOUND[finding.id]?.(finding.values || {});
  return words ? { id: finding.id, confidence: finding.confidence || "estimated", ...words } : null;
}

// ── Checked, nothing unusual · not checked ──

const NOTHING = {
  chapters: "Chapters: the room's own chat was even across them.",
  friction: "Production: no burst of sound or connection complaints, and no late start.",
};

const NOT_CHECKED = {
  chapters: {
    no_chapters: "Chapters: none found yet. Run “Analyse session” to find them.",
    clock: "Chapters: the recording's clock couldn't be matched to the session's.",
    labels: "Chapters: needs “Analyse session”, to tell the room's own chat from greetings and replies.",
    short: "Chapters: the session is under 10 minutes.",
    few_messages: "Chapters: too little unprompted chat to compare them.",
    few_chapters: "Chapters: fewer than two chapters long enough to compare.",
  },
  friction: {
    labels: "Production: needs “Analyse session” to read the chat's sound and connection complaints. It started on time.",
  },
};

/** The collapsed list: what was checked and found nothing, then what
 * couldn't be checked and why. */
export function checkedLines(body) {
  const nothing = (body?.nothing || []).map((item) => NOTHING[item.id]).filter(Boolean);
  const notChecked = (body?.notChecked || []).map((item) => NOT_CHECKED[item.id]?.[item.reason]).filter(Boolean);
  return { nothing, notChecked };
}

/** Everything the section shows, from `GET /record/findings` (or null). */
export function findingsView(body) {
  const found = (body?.found || []).map(foundFinding).filter(Boolean);
  const { nothing, notChecked } = checkedLines(body);
  return { found, nothing, notChecked, empty: !found.length && !nothing.length && !notChecked.length };
}
