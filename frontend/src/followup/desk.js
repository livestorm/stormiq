// The question desk's words: pure functions over `GET /record/questions`
// (docs/api.md, "The question desk"). The server decides each question's
// status; the page only filters, counts what the server counted and words it.

import { clockLabel } from "../report/chartGeometry";

export const FILTERS = [
  { key: "open", label: "Still open" },
  { key: "answered", label: "Answered" },
  { key: "all", label: "All" },
];

// Where a question was asked: the Q&A tab, or the chat (a message the
// session analysis labelled a question). Each has its colour on the desk.
export const SOURCES = [
  { key: "all", label: "Both" },
  { key: "qa", label: "Q&A tab", icon: "help-circle" },
  { key: "chat", label: "Chat", icon: "message-circle" },
];

/** `{key, label, icon}` for a question's source ("qa" unless the server says "chat"). */
export function sourceOf(question = {}) {
  return SOURCES.find((source) => source.key === (question.source === "chat" ? "chat" : "qa"));
}

const ANSWERED = new Set(["written", "aloud", "here"]);
// Still open for the host: a passage of the talk that seems to answer a
// "covered" question isn't an answer until they confirm it.
const UNANSWERED = new Set(["covered", "open"]);

function count(value) {
  return Number.isFinite(Number(value)) ? Number(value) : 0;
}

function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many}`;
}

function bySource(questions, source) {
  return source === "qa" || source === "chat" ? questions.filter((q) => sourceOf(q).key === source) : questions;
}

/** The count each filter shows: the server's summary, or, with one source
 * picked, that source's questions in the list. */
export function filterCounts(summary = {}, { questions = [], source = "all" } = {}) {
  if (source === "qa" || source === "chat") {
    const kept = bySource(questions, source);
    return {
      open: kept.filter((q) => UNANSWERED.has(q.status)).length,
      answered: kept.filter((q) => ANSWERED.has(q.status)).length,
      all: kept.length,
    };
  }
  return {
    open: count(summary.open) + count(summary.covered),
    answered: count(summary.written) + count(summary.aloud) + count(summary.here),
    all: count(summary.total),
  };
}

/** The questions a filter keeps, from one source or both, in the server's order. */
export function filterQuestions(questions = [], filter = "open", source = "all") {
  const kept = bySource(questions, source);
  if (filter === "all") return kept;
  if (filter === "answered") return kept.filter((q) => ANSWERED.has(q.status));
  return kept.filter((q) => UNANSWERED.has(q.status));
}

/** How many of the questions a status filter keeps come from each source:
 * `{all, qa, chat}`, for the source switch. */
export function sourceCounts(questions = [], filter = "open") {
  const kept = filterQuestions(questions, filter);
  const chat = kept.filter((q) => sourceOf(q).key === "chat").length;
  return { all: kept.length, qa: kept.length - chat, chat };
}

/** "5 questions: 1 still open · 1 covered in the talk, not marked · 2 answered in writing · 1 answered aloud". */
export function summaryLine(summary = {}) {
  const total = count(summary.total);
  if (!total) return "No audience questions in the Q&A or the chat.";
  const parts = [
    [count(summary.open), "still open"],
    [count(summary.covered), "covered in the talk, not marked"],
    [count(summary.written), "answered in writing"],
    [count(summary.aloud), "answered aloud"],
    [count(summary.here), "answered here"],
  ]
    .filter(([n]) => n > 0)
    .map(([n, words]) => `${n} ${words}`);
  return `${plural(total, "question", "questions")}: ${parts.join(" · ")}`;
}

/** "1 replay or slides request in the chat, counted apart.", or "". */
export function logisticsLine(summary = {}) {
  const n = count(summary.logistics);
  if (!n) return "";
  return `${plural(n, "replay or slides request", "replay or slides requests")} in the chat, counted apart.`;
}

/** Why the chat's questions are missing, from the labels' state, or "". */
export function chatNotice(state) {
  if (state === "none") return "Chat questions show here once the session is analysed (Analyse session, on the Report).";
  if (state === "stale") return "The chat changed since it was analysed. Analyse it again on the Report to list its questions.";
  return "";
}

/** Why answers from the talk are missing, from their state, or "". Said
 * only when the chat's own notice doesn't already say the same thing. */
export function locatedNotice(summary = {}) {
  if (summary.located !== "stale" || summary.chatQuestions === "stale") return "";
  return "The session changed since it was analysed. Analyse it again on the Report to find the questions answered in the talk.";
}

const STATUS = {
  written: { status: "measured", label: "Answered in writing" },
  aloud: { status: "measured", label: "Answered aloud" },
  here: { status: "measured", label: "Answered here" },
  covered: { status: "estimated", label: "Covered in the talk, not marked" },
  open: { status: "check", label: "Still open" },
};

/** The status word for a question: `{status, label}` for `StatusWord`. */
export function statusOf(question) {
  return STATUS[question?.status] || STATUS.open;
}

/** "Theo P. · 12:05" (a time before the start reads "Before"); the source
 * is its own badge (`sourceOf`). */
export function metaLine(question = {}) {
  const t = question.t;
  const time = typeof t === "number" && Number.isFinite(t) ? (t < 0 ? "Before" : clockLabel(t)) : "";
  return [question.author, time].filter(Boolean).join(" · ");
}

/** Whether the host can mark it here: open, or marked here before; and
 * saved by its Livestorm id (`canSave`). Livestorm's own answers stay. */
export function canMark(question = {}) {
  return Boolean(question.canSave) && (question.status === "open" || question.status === "here");
}

/** The located passage's heading: "Seems answered in the talk at 5:19"
 * (not confirmed yet), "Answered in the talk at 5:19" (confirmed, or marked
 * answered aloud in Livestorm), or "" without a passage. */
export function locatedLabel(question = {}) {
  const located = question.located;
  if (!located) return "";
  const t = located.t;
  const time = typeof t === "number" && Number.isFinite(t) && t >= 0 ? ` at ${clockLabel(t)}` : "";
  return question.status === "covered" ? `Seems answered in the talk${time}` : `Answered in the talk${time}`;
}

/** What the host can do with a located passage: confirm it (a "covered"
 * question only) and set it aside ("Not this"); saved by Livestorm id. */
export function locatedActions(question = {}) {
  if (!question.canSave || !question.located) return { confirm: false, reject: false };
  if (question.status === "covered") return { confirm: true, reject: true };
  return { confirm: false, reject: question.status === "aloud" };
}

/** The download menu's choices, `[{scope, label, about, disabled}]`: the
 * questions still open (none to download when nothing is open), every
 * question with its status, or every question with its status and its
 * answer (for a follow-up email). The server writes the file. */
export function csvChoices(counts = {}) {
  return [
    { scope: "open", label: "Open questions", about: "To answer after the session", disabled: !count(counts.open) },
    { scope: "all", label: "All questions", about: "With each one's status", disabled: !count(counts.all) },
    {
      scope: "answers",
      label: "All questions with answers",
      about: "Your answers, Livestorm's and the talk's, for a follow-up email",
      disabled: !count(counts.all),
    },
  ];
}
