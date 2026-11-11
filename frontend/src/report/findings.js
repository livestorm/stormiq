// The words of the Report's findings (`GET /record/findings`,
// `record/findings.py`): every sentence comes from a template here (English)
// or in `findingsFr.js` (French), filled with the server's values, and each
// found finding gets a "Next time" line from the curated library
// (`nextTime.js`). Nothing is computed but a time label and a multiple of
// 100 for the index.

import { clockLabel, secondsLeft } from "./chartGeometry";
import { FR } from "./findingsFr";
import { nextTime } from "./nextTime";

const plural = (count, one, many) => `${count} ${count === 1 ? one : many}`;
// "from 3 people", or nothing when the authors couldn't be told apart.
const fromPeople = (people) => (people > 0 ? ` from ${plural(people, "person", "people")}` : "");
const minutes = (seconds) => Math.max(1, Math.round(seconds / 60));
const times = (index) => `${(index / 100).toFixed(1).replace(/\.0$/, "")}×`;

// ── Found ──

function chaptersFinding(values) {
  const rows = values.chapters || [];
  const usual = values.usualPerMinute;
  const high = rows.find((row) => row.title === values.high);
  const low = rows.find((row) => row.title === values.low);
  const headline = high ? `“${high.title}” drew the room in` : `The room went quiet during “${low.title}”`;
  const lines = [];
  if (high) {
    lines.push(
      `${high.perMinute} chat messages a minute${fromPeople(high.chatters)}, ${times(high.index)} the usual chapter (${usual} a minute).`,
    );
  }
  if (low) lines.push(`During “${low.title}” the chat fell to ${low.perMinute} a minute, ${times(low.index)} the usual.`);
  lines.push("Only what the room wrote on its own counts: not greetings, logistics, sound complaints or replies to a question from the stage.");
  const top = Math.max(usual * 2 || 0, ...rows.map((row) => row.perMinute || 0)) || 1;
  const chart = {
    title: "The room's own chat, messages a minute, by chapter",
    usual: usual === null || usual === undefined ? "" : `usual: ${usual} a minute`,
    usualLeft: usual ? (usual / top) * 100 : null,
    rows: rows.map((row) => ({
      title: row.title,
      when: `${clockLabel(row.t)} · ${minutes(row.seconds)} min`,
      width: row.perMinute === null ? 0 : Math.min((row.perMinute / top) * 100, 100),
      value: row.perMinute !== null ? `${row.perMinute}/min` : row.qa ? `Q&A · ${plural(row.questions, "question", "questions")}` : "under 2 min",
      tone: row.title === values.high ? "high" : row.title === values.low ? "low" : "",
    })),
  };
  return { headline, lines, chart, rows: [] };
}

function frictionFinding(values) {
  const found = values.incidents || [];
  const late = values.lateStart;
  const top = found.reduce((best, item) => (best === null || item.messages > best.messages ? item : best), null);
  let headline = `The session started ${minutes(late?.lateS || 0)} minutes late`;
  if (top && top.people > 0) headline = `${plural(top.people, "person", "people")} flagged a sound or connection problem at ${clockLabel(top.start)}`;
  else if (top) headline = `${plural(top.messages, "message", "messages")} about sound or connection from ${clockLabel(top.start)}`;
  const rows = found.map((item) => {
    const parts = [`${plural(item.messages, "message", "messages")}${fromPeople(item.people)}`];
    if (values.speech) parts.push(item.deadAirS > 0 ? `${Math.round(item.deadAirS)} s of silence on stage` : "no silence on stage");
    return `${clockLabel(item.start)}–${clockLabel(item.end)} · ${parts.join(" · ")}`;
  });
  if (late && top) rows.push(`Started ${minutes(late.lateS)} min late · ${plural(late.waitingMessages, "message", "messages")} in the waiting room`);
  const lines = top
    ? ["Messages the analysis labelled as sound, video or connection issues, grouped when they came within two minutes of each other."]
    : [`${plural(late.waitingMessages, "message", "messages")} from the audience in the waiting room before it started.`];
  return { headline, lines, chart: null, rows };
}

function monologueFinding(values) {
  const length = minutes(values.seconds);
  return {
    headline: `${length} minutes without bringing the room in, and the chat fell`,
    lines: [
      `From ${clockLabel(values.start)} to ${clockLabel(values.end)}, one speaker talked with no prompt to the room and no audience question taken. The room's own chat went from ${values.firstPerMinute} to ${values.lastPerMinute} messages a minute between the first and the last third.`,
      "Chat stands in for attention here: a quiet room may still be listening.",
    ],
    chart: null,
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
    chart: null,
    rows: rows.map(promptRow),
  };
}

function momentRow(moment, usual) {
  const parts = [`${plural(moment.messages, "message", "messages")}${fromPeople(moment.people)}`];
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
    chart: null,
    rows: moments.map((moment) => momentRow(moment, values.usualPerMinute)),
  };
}

function talkLine(topic) {
  if (!topic.talkS) return "no answer found in the talk";
  const where = topic.chapter ? ` in “${topic.chapter}”` : "";
  return `${topic.talkS < 60 ? `${Math.round(topic.talkS)} s` : `${minutes(topic.talkS)} min`} of the talk answered it${where}`;
}

function demandFinding(values) {
  const topics = values.topics || [];
  const unmet = topics.filter((topic) => (values.unmet || []).includes(topic.label));
  const top = unmet[0];
  return {
    headline:
      unmet.length === 1
        ? `${top.people > 1 ? plural(top.people, "person", "people") : plural(top.asked, "question", "questions")} asked about “${top.label}”, and most got no answer`
        : `${unmet.length} topics the room kept asking about got few answers`,
    lines: [
      "The analysis grouped the questions that ask the same thing. A topic stands out when half or more of its questions have no answer: none written, none marked answered, none confirmed in the talk.",
    ],
    chart: null,
    rows: topics.map(
      (topic) =>
        `“${topic.label}” · ${plural(topic.asked, "question", "questions")}${fromPeople(topic.people)} · ${topic.unmet} unanswered · ${talkLine(topic)}`,
    ),
  };
}

const FOUND = { demand: demandFinding, moments: momentsFinding, chapters: chaptersFinding, friction: frictionFinding, monologue: monologueFinding, prompts: promptsFinding };

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
  demand: (values) =>
    (values?.topics || []).length
      ? `What the room asked for: every topic several people asked about got its answers.`
      : "What the room asked for: no topic drew 3 or more questions.",
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
  demand: {
    no_reading: "What the room asked for: needs “Analyse session” on the session as it is now, to group the questions.",
  },
};

const EN = {
  found: FOUND,
  nothing: NOTHING,
  notChecked: NOT_CHECKED,
  labels: { title: "What stood out", quiet: "Nothing stood out from the session's own pattern.", checked: "Checked, nothing unusual", notChecked: "Not checked", notCheckedNote: (n) => `${n === 1 ? "1 check" : `${n} checks`} couldn't run: why is in “Method and data quality”.`, next: "Next time", measured: "Measured", estimated: "Estimated" },
};

const WORDS = { en: EN, fr: FR };
const words = (lang) => WORDS[lang] || EN;

/** A found finding in words, in `lang` ("en" or "fr"): `{id, headline,
 * lines, chart, rows, confidence, next}`; `next` is the "Next time" line
 * picked with `seed` (the session's id). */
export function foundFinding(finding, lang = "en", seed = "") {
  const made = words(lang).found[finding.id]?.(finding.values || {});
  if (!made) return null;
  return { id: finding.id, confidence: finding.confidence || "estimated", ...made, next: nextTime(finding, lang, seed) };
}

const CHECK_ICONS = { demand: "help-circle", chapters: "list", friction: "zap", monologue: "timer", moments: "activity", prompts: "message-circle" };

// "Chapters: the room's…" (English) or "Chapitres : le chat…" (French):
// the topic before the first colon, the verdict after it.
function split(line) {
  const match = /^(.+?)\s?:\s(.+)$/.exec(line);
  if (!match) return { topic: "", text: line };
  const text = match[2];
  return { topic: match[1], text: text.charAt(0).toUpperCase() + text.slice(1) };
}

/** What was checked and found nothing (`nothing`, as tiles: `{id, icon,
 * line, topic, text}`), then what couldn't be checked and why
 * (`notChecked`, `{id, line, topic, text}`); `line` is the whole sentence. */
export function checkedLines(body, lang = "en") {
  const { nothing: said, notChecked: why } = words(lang);
  const nothing = (body?.nothing || [])
    .map((item) => {
      const line = said[item.id]?.(item.values);
      return line ? { id: item.id, icon: CHECK_ICONS[item.id] || "check", line, ...split(line) } : null;
    })
    .filter(Boolean);
  const notChecked = (body?.notChecked || [])
    .map((item) => {
      const line = why[item.id]?.[item.reason];
      return line ? { id: item.id, line, ...split(line) } : null;
    })
    .filter(Boolean);
  return { nothing, notChecked };
}

/** Everything the section shows, from `GET /record/findings` (or null), in
 * `lang`, with the section's own labels. */
export function findingsView(body, lang = "en") {
  const seed = String(body?.sessionId || "");
  const found = (body?.found || []).map((finding) => foundFinding(finding, lang, seed)).filter(Boolean);
  const { nothing, notChecked } = checkedLines(body, lang);
  return { found, nothing, notChecked, labels: words(lang).labels, empty: !found.length && !nothing.length && !notChecked.length };
}

/** What the findings mark on the Pulse, in percent of an axis of `count`
 * minutes: the monologue stretch that was found (`bands`), every prompt to
 * the room that was checked (`prompts`, `missed` when the room answered
 * after the speaker moved on), and the moments, numbered (`pins`): the
 * key moments told under the chart (`momentPins`, from `GET
 * /record/moments`) when they are loaded, else the unprompted moments
 * found. */
export function pulseMarks(body, count, told = null) {
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
  const pins = told
    ? told.map((pin) => ({ n: pin.n, left: (secondsLeft(pin.start, count) + secondsLeft(pin.end, count)) / 2, title: pin.title }))
    : (moments?.values?.moments || []).map((moment) => ({
      n: moment.n,
      left: (secondsLeft(moment.start, count) + secondsLeft(moment.end, count)) / 2,
      title: `${moment.n}. ${clockLabel(moment.start)}: ${plural(moment.messages, "message", "messages")}${moment.quote ? ` while the speaker was saying “${moment.quote}”` : ""}`,
    }));
  return { bands, prompts: marks, pins };
}
