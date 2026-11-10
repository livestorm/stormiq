// The v1 Report's "The questions" section, worded from `record.measures`:
// the question count (Q&A plus the chat's questions about the content, the
// chat's questions about the session itself counted apart), how the Q&A's
// questions were answered, and the median time to a written answer.
//
// The server counts everything (docs/product.md §13: `m.questions_total`,
// `m.questions_qa`, `m.time_to_answer`); this module only words the rows.
// The ledger with each question's answer status comes with the analysis
// that reads the transcript (1e). Pure functions.

import { labelStatus } from "./room";
import { HIDDEN, formatPercent, formatSeconds, hiddenReason, plural, statusFor } from "./vitals";

function rowsByKey(measure) {
  return Object.fromEntries((measure?.rows || []).map((row) => [row.key, row.questions]));
}

/** The count line: `{value, parts, apart, note, status, info}`. Q&A only,
 * said as such, until the analysis labelled the chat. Null without either
 * measure. */
export function questionCount(measures = {}) {
  const total = measures["m.questions_total"];
  const qa = measures["m.questions_qa"];
  const qaCount = qa && qa.state !== "unavailable" ? qa.den : null;
  if (total && !HIDDEN.has(total.state)) {
    const rows = rowsByKey(total);
    return {
      value: String(total.value),
      parts: [`${rows.qa ?? 0} in the Q&A`, `${rows.chat ?? 0} in the chat`],
      apart: rows.chatLogistics
        ? `${plural(rows.chatLogistics, "chat question", "chat questions")} about the session itself (a replay, the slides), counted apart.`
        : "",
      note: "",
      status: labelStatus(total),
      info: "Questions asked in the Q&A, plus the audience's chat messages that the analysis (AI) labelled as questions about the content. Questions about the session itself are counted apart. Open “Questions” in the chat themes to read the chat's.",
    };
  }
  if (qaCount == null) return null;
  return {
    value: String(qaCount),
    parts: ["in the Q&A"],
    apart: "",
    note: total?.coverage?.missing?.includes("labels") ? hiddenReason(total) : "",
    status: "measured",
    info: "Questions asked in the Q&A. The analysis adds the questions asked in the chat.",
  };
}

const ANSWER_ROWS = [
  { key: "written", label: "Answered in writing" },
  { key: "oral", label: "Answered aloud" },
  { key: "marked", label: "Marked answered" },
  { key: "open", label: "Still open" },
];

/** How the Q&A's questions were answered: `{rows, rate, delay, status,
 * info, note}`. A row is `{key, label, questions, open}`. Null when the
 * questions weren't fetched. */
export function qaAnswers(measures = {}) {
  const qa = measures["m.questions_qa"];
  if (!qa || qa.state === "unavailable") return null;
  if (!qa.den) return { rows: [], rate: "", delay: "", status: "", info: "", note: "No question in the Q&A." };
  const counts = rowsByKey(qa);
  const answer = measures["m.time_to_answer"];
  const delay = answer && !HIDDEN.has(answer.state) ? formatSeconds(answer.value) : "";
  let note = "";
  if (answer?.state === "insufficient") note = "Too few written answers with a time for a median delay.";
  return {
    rows: ANSWER_ROWS.filter((row) => counts[row.key] != null).map((row) => ({
      key: row.key,
      label: row.label,
      questions: String(counts[row.key]),
      open: row.key === "open",
    })),
    rate: HIDDEN.has(qa.state) ? "" : formatPercent(qa.value),
    delay,
    note,
    status: HIDDEN.has(qa.state) ? "" : statusFor(qa),
    info: "Q&A questions answered in writing, marked answered aloud, or marked answered, out of all Q&A questions. The delay is the median time from a question to its written answer, from 3 answers.",
  };
}

/** The collapsed card's one line and key chip. */
export function questionsSummary(measures = {}) {
  const count = questionCount(measures);
  const open = rowsByKey(measures["m.questions_qa"]).open;
  const qaShown = measures["m.questions_qa"] && measures["m.questions_qa"].state !== "unavailable";
  return {
    takeaway: count ? `${plural(Number(count.value), "question", "questions")}: ${count.parts.join(", ")}.` : "What the audience asked.",
    chip: qaShown && open != null ? `${open} still open in Q&A` : "",
  };
}
