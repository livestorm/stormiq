// The words of the Report's findings (`GET /record/findings`,
// `record/findings.py`): every sentence comes from a template here, filled
// with the server's values. Nothing is computed but a time label and a
// multiple of 100 for the index.

import { clockLabel, secondsLeft } from "./chartGeometry";

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

function monologueFinding(values) {
  const length = minutes(values.seconds);
  return {
    headline: `${length} minutes without bringing the room in, and the chat fell`,
    lines: [
      `From ${clockLabel(values.start)} to ${clockLabel(values.end)}, one speaker talked with no prompt to the room and no audience question taken. The room's own chat went from ${values.firstPerMinute} to ${values.lastPerMinute} messages a minute between the first and the last third.`,
      "Chat stands in for attention here: a quiet room may still be listening.",
    ],
    bars: [],
    rows: [],
  };
}

function promptRow(row) {
  const parts = [];
  parts.push(row.waitS === null ? "no speech after it" : `waited ${Math.round(row.waitS)} s`);
  parts.push(`${plural(row.messages, "message", "messages")} in the next 2 min (about ${Math.round(row.expected)} usual)`);
  if (row.waitS !== null) parts.push(`${row.late} after the speaker went on`);
  return `${clockLabel(row.t)} “${row.quote}” · ${parts.join(" · ")}`;
}

function promptsFinding(values) {
  const missed = new Set(values.missed || []);
  const rows = (values.prompts || []).filter((row) => missed.has(row.t));
  return {
    headline:
      rows.length === 1
        ? "The room answered after the speaker had moved on"
        : `The room answered ${rows.length} prompts after the speaker had moved on`,
    lines: [
      "When the speaker asked the room something, most replies came after they started talking again. A few more seconds of silence would have let the room in.",
      "Replies aren't marked one by one: the messages over the session's usual rate stand for them.",
    ],
    bars: [],
    rows: rows.map(promptRow),
  };
}

function momentRow(moment, usual) {
  const parts = [`${plural(moment.messages, "message", "messages")} from ${plural(moment.people, "person", "people")}`];
  if (usual > 0) parts.push(`${Math.round(moment.peak / usual)}× the usual minute`);
  const head = `${moment.n}. ${clockLabel(moment.start)}–${clockLabel(moment.end)} · ${parts.join(" · ")}`;
  return moment.quote ? `${head} · while the speaker was saying “${moment.quote}”` : head;
}

function momentsFinding(values) {
  const moments = values.moments || [];
  const lines = [
    `Minutes where the room wrote far more than its usual ${values.usualPerMinute} a minute, on its own: not replies to a question from the stage, not greetings.`,
  ];
  if (!values.quotes) lines.push("The recording's clock isn't checked to the second, so the words being said aren't shown.");
  return {
    headline:
      moments.length === 1
        ? `The room leaned in at ${clockLabel(moments[0].start)}, unprompted`
        : `The room leaned in ${moments.length} times, unprompted`,
    lines,
    bars: [],
    rows: moments.map((moment) => momentRow(moment, values.usualPerMinute)),
  };
}

const FOUND = { moments: momentsFinding, chapters: chaptersFinding, friction: frictionFinding, monologue: monologueFinding, prompts: promptsFinding };

/** A found finding in words: `{id, headline, lines, bars, rows, confidence}`. */
export function foundFinding(finding) {
  const words = FOUND[finding.id]?.(finding.values || {});
  return words ? { id: finding.id, confidence: finding.confidence || "estimated", ...words } : null;
}

// ── Checked, nothing unusual · not checked ──

const NOTHING = {
  chapters: () => "Chapters: the room's own chat was even across them.",
  friction: () => "Production: no burst of sound or connection complaints, and no late start.",
  monologue: (values) =>
    values?.firstPerMinute === undefined
      ? `Monologues: the longest stretch without bringing the room in was ${minutes(values?.seconds || 0)} min.`
      : `Monologues: the longest stretch ran ${minutes(values.seconds)} min, and the chat held up.`,
  moments: (values) => {
    const others = [];
    if (values?.replies) others.push(`${plural(values.replies, "reply", "replies")} to a prompt`);
    if (values?.waves) others.push(`${plural(values.waves, "wave", "waves")} of greetings or logistics`);
    return `Moments: no burst beyond the room's usual ${values?.usualPerMinute ?? 0} a minute${others.length ? `, other than ${others.join(" and ")}` : ""}.`;
  },
  prompts: (values) => {
    const count = (values?.prompts || []).length;
    return `Prompts to the room: it had time to answer ${count === 1 ? "the one prompt" : `all ${count} prompts`}.`;
  },
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
  monologue: {
    clock: "Monologues: needs the transcript placed on the session's clock.",
    labels: "Monologues: needs “Analyse session”, to tell the room's own chat from greetings.",
    no_speech: "Monologues: the transcript has no timed speech.",
  },
  moments: {
    labels: "Moments: needs “Analyse session”, to tell the room's own bursts from greetings and replies.",
    clock: "Moments: the session's length is unknown.",
    few_messages: "Moments: fewer than 30 audience messages, too few to find a burst.",
  },
  prompts: {
    clock: "Prompts to the room: needs the recording's clock checked to the second.",
    no_prompts: "Prompts to the room: the analysis found none in the talk.",
  },
};

/** The collapsed list: what was checked and found nothing, then what
 * couldn't be checked and why. */
export function checkedLines(body) {
  const nothing = (body?.nothing || []).map((item) => NOTHING[item.id]?.(item.values)).filter(Boolean);
  const notChecked = (body?.notChecked || []).map((item) => NOT_CHECKED[item.id]?.[item.reason]).filter(Boolean);
  return { nothing, notChecked };
}

/** Everything the section shows, from `GET /record/findings` (or null). */
export function findingsView(body) {
  const found = (body?.found || []).map(foundFinding).filter(Boolean);
  const { nothing, notChecked } = checkedLines(body);
  return { found, nothing, notChecked, empty: !found.length && !nothing.length && !notChecked.length };
}

/** What the findings mark on the Pulse, in percent of an axis of `count`
 * minutes: the monologue stretch that was found (`bands`), every prompt to
 * the room that was checked (`prompts`, `missed` when the room answered
 * after the speaker moved on), and the unprompted moments, numbered
 * (`pins`). */
export function pulseMarks(body, count) {
  const all = [...(body?.found || []), ...(body?.nothing || [])];
  const monologue = (body?.found || []).find((item) => item.id === "monologue");
  const prompts = all.find((item) => item.id === "prompts");
  const missed = new Set(prompts?.values?.missed || []);
  const bands = monologue
    ? [
        {
          left: secondsLeft(monologue.values.start, count),
          width: secondsLeft(monologue.values.end, count) - secondsLeft(monologue.values.start, count),
          label: `${minutes(monologue.values.seconds)} min, no prompt · chat ${monologue.values.firstPerMinute} → ${monologue.values.lastPerMinute}/min`,
        },
      ]
    : [];
  const marks = (prompts?.values?.prompts || []).map((row) => ({
    left: secondsLeft(row.t, count),
    missed: missed.has(row.t),
    title: `${clockLabel(row.t)} prompt to the room: “${row.quote}”`,
  }));
  const moments = (body?.found || []).find((item) => item.id === "moments");
  const pins = (moments?.values?.moments || []).map((moment) => ({
    n: moment.n,
    left: (secondsLeft(moment.start, count) + secondsLeft(moment.end, count)) / 2,
    title: `${moment.n}. ${clockLabel(moment.start)}: ${plural(moment.messages, "message", "messages")}${moment.quote ? ` while the speaker was saying “${moment.quote}”` : ""}`,
  }));
  return { bands, prompts: marks, pins };
}
