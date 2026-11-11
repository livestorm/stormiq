// The question desk's words: pure functions over `GET /record/questions`
// (docs/api.md, "The question desk"). The server decides each question's
// status; the page only filters, counts what the server counted and words it.

import { clockLabel } from "../report/chartGeometry";

export const FILTERS = [
  { key: "open", label: "Still open" },
  { key: "answered", label: "Answered" },
  { key: "all", label: "All" },
];

const ANSWERED = new Set(["written", "aloud", "here"]);

function count(value) {
  return Number.isFinite(Number(value)) ? Number(value) : 0;
}

function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many}`;
}

/** The count each filter shows, from the server's summary. */
export function filterCounts(summary = {}) {
  return {
    open: count(summary.open),
    answered: count(summary.written) + count(summary.aloud) + count(summary.here),
    all: count(summary.total),
  };
}

/** The questions a filter keeps, in the server's order. */
export function filterQuestions(questions = [], filter = "open") {
  if (filter === "all") return questions;
  if (filter === "answered") return questions.filter((q) => ANSWERED.has(q.status));
  return questions.filter((q) => q.status === "open");
}

/** "5 questions: 2 still open · 2 answered in writing · 1 answered aloud". */
export function summaryLine(summary = {}) {
  const total = count(summary.total);
  if (!total) return "No audience questions in the Q&A or the chat.";
  const parts = [
    [count(summary.open), "still open"],
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

const STATUS = {
  written: { status: "measured", label: "Answered in writing" },
  aloud: { status: "measured", label: "Answered aloud" },
  here: { status: "measured", label: "Answered here" },
  open: { status: "check", label: "Still open" },
};

/** The status word for a question: `{status, label}` for `StatusWord`. */
export function statusOf(question) {
  return STATUS[question?.status] || STATUS.open;
}

/** "Theo P. · Q&A · 12:05" (a time before the start reads "Before"). */
export function metaLine(question = {}) {
  const t = question.t;
  const time = typeof t === "number" && Number.isFinite(t) ? (t < 0 ? "Before" : clockLabel(t)) : "";
  return [question.author, question.source === "chat" ? "Chat" : "Q&A", time].filter(Boolean).join(" · ");
}

/** Whether the host can mark it here: open, or marked here before; and
 * saved by its Livestorm id (`canSave`). Livestorm's own answers stay. */
export function canMark(question = {}) {
  return Boolean(question.canSave) && (question.status === "open" || question.status === "here");
}
