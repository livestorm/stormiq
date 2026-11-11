// The v1 Report's "The questions" section, worded from the question desk
// (`GET /record/questions`, the same read as Follow-up's desk): every
// audience question, from the Q&A and the chat, grouped as still open or
// answered, each with who asked, when, and its answer (Livestorm's written
// answer, the host's, or the passage of the talk that answers it).
//
// The server decides each question's status and counts (docs/api.md, "The
// question desk"); this module only groups and words them, with the desk's
// own words (`followup/desk.js`). Pure functions.

import {
  FILTERS,
  chatNotice,
  filterCounts,
  filterQuestions,
  locatedLabel,
  locatedNotice,
  logisticsLine,
  metaLine,
  statusOf,
} from "../followup/desk";
import { HIDDEN, formatSeconds, plural } from "./vitals";

const GROUP_TITLES = { open: "Still open", answered: "Answered" };

/** One question as the Report lists it: `{id, text, source, who, status,
 * answers}`; `answers` is `[{label, text, quote}]`, in the order a host
 * reads them: their own answer, Livestorm's written one, then the talk. */
export function questionItem(question = {}) {
  const answers = [];
  if (question.reply) answers.push({ label: "Your answer", text: question.reply, quote: false });
  if (question.answer) answers.push({ label: "Written answer", text: question.answer, quote: false });
  if (question.located?.quote) answers.push({ label: locatedLabel(question), text: question.located.quote, quote: true });
  return {
    id: question.id,
    text: question.text || "",
    source: question.source === "chat" ? "chat" : "qa",
    who: metaLine(question),
    status: statusOf(question),
    answers,
  };
}

/** The two groups, `[{key, title, count, items}]`, each only when it has a
 * question: still open first (with those the talk seems to answer, which
 * the host hasn't confirmed), then answered. Questions in the server's
 * order. */
export function questionGroups(body) {
  const questions = body?.questions || [];
  const counts = filterCounts(body?.summary || {});
  return FILTERS.filter((filter) => filter.key !== "all")
    .map((filter) => ({
      key: filter.key,
      title: GROUP_TITLES[filter.key],
      count: counts[filter.key],
      items: filterQuestions(questions, filter.key).map(questionItem),
    }))
    .filter((group) => group.items.length);
}

/** The collapsed card's one line and key chip, from the desk's summary;
 * before the desk loads, the record's own count. */
export function questionsHeadline(body, measures = {}) {
  if (body?.summary) {
    const counts = filterCounts(body.summary);
    if (!counts.all) return { takeaway: "No audience question in the Q&A or the chat.", chip: "" };
    return {
      takeaway: `${plural(counts.all, "question", "questions")}: ${counts.answered} answered, ${counts.open} still open.`,
      chip: counts.open ? `${counts.open} still open` : "All answered",
    };
  }
  const total = measures["m.questions_total"];
  if (total && !HIDDEN.has(total.state)) return { takeaway: `${plural(Number(total.value), "question", "questions")}.`, chip: "" };
  return { takeaway: "What the audience asked, and what was answered.", chip: "" };
}

/** "Written answers came after 1 min 35 s (median)." or "". */
export function answerDelay(measures = {}) {
  const answer = measures["m.time_to_answer"];
  if (!answer || HIDDEN.has(answer.state)) return "";
  const delay = formatSeconds(answer.value);
  return delay ? `Written answers came ${delay} after the question (median).` : "";
}

/** The notes under the list: why some questions may be missing (the chat
 * not analysed yet, or changed since), and the replay or slides requests
 * counted apart. */
export function questionNotes(body) {
  const summary = body?.summary || {};
  return [chatNotice(summary.chatQuestions), locatedNotice(summary), logisticsLine(summary)].filter(Boolean);
}
