// The editable FAQ's words and edits (`GET`/`PUT /record/faq`): pure
// functions, no Vue, no network. The server drafts the entries from the
// question desk, validates and saves them (`record/faq_entries.py`); the page
// edits a copy of them here.
//
// An entry is `{id, question, answer, source, t, origin, deskIds}`: `source`
// one of SOURCES, `t` the time of a passage of the talk (or of a question
// answered live) in seconds on the session clock, `origin` "desk" or
// "manual", `deskIds` the desk questions it came from.

export const MAX_ENTRIES = 200;
export const MAX_QUESTION_LENGTH = 500;
export const MAX_ANSWER_LENGTH = 4000;

export const SOURCES = ["written", "host", "talk", "aloud", "manual", "none"];

const SOURCE_WORDS = {
  written: "Written answer",
  host: "Host's reply",
  talk: "From the talk",
  aloud: "Answered live",
  manual: "Added by you",
  none: "Not answered yet",
};

const SOURCE_ICONS = {
  written: "pen-line",
  host: "message-circle",
  talk: "play",
  aloud: "play",
  manual: "user-check",
  none: "help-circle",
};

/** `m:ss`, or `h:mm:ss` from an hour; "" without a time (or before 0). */
export function replayTime(seconds) {
  if (seconds == null || seconds === "") return "";
  const t = Number(seconds);
  if (!Number.isFinite(t) || t < 0) return "";
  const total = Math.floor(t);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = String(total % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${secs}` : `${minutes}:${secs}`;
}

function hasAnswer(entry) {
  return Boolean(String(entry?.answer ?? "").trim());
}

/** The source chip of an entry: `{label, icon}`, "From the talk · 12:04". */
export function sourceChip(entry) {
  const source = hasAnswer(entry) ? (SOURCES.includes(entry.source) && entry.source !== "none" ? entry.source : "manual") : "none";
  const at = source === "talk" || source === "aloud" ? replayTime(entry.t) : "";
  return { label: at ? `${SOURCE_WORDS[source]} · ${at}` : SOURCE_WORDS[source], icon: SOURCE_ICONS[source], source };
}

/** An entry with a new answer: an emptied answer makes it open (`none`, no
 * time); a typed answer on an open entry is the user's (`manual`). */
export function withAnswer(entry, answer) {
  const text = String(answer ?? "");
  if (!text.trim()) return { ...entry, answer: text, source: "none", t: null };
  if (!entry.source || entry.source === "none") return { ...entry, answer: text, source: "manual", t: null };
  return { ...entry, answer: text };
}

/** A new, empty entry the user adds, with an id none of `entries` has. */
export function newEntry(entries = []) {
  const ids = new Set(entries.map((entry) => entry.id));
  let n = entries.length + 1;
  while (ids.has(`m-${n}`)) n += 1;
  return { id: `m-${n}`, question: "", answer: "", source: "none", t: null, origin: "manual", deskIds: [] };
}

/** `entries` with the one at `index` moved by `delta` (−1 up, +1 down). */
export function moveEntry(entries, index, delta) {
  const to = index + delta;
  if (index < 0 || index >= entries.length || to < 0 || to >= entries.length) return entries.slice();
  const out = entries.slice();
  const [item] = out.splice(index, 1);
  out.splice(to, 0, item);
  return out;
}

/** `entries` without the one at `index`. */
export function removeEntry(entries, index) {
  return entries.filter((_, i) => i !== index);
}

/** `entries` with `entry` put back where it was (`index`, kept within the
 * list): the undo of a removal. An entry already there is not added twice. */
export function restoreEntry(entries, entry, index) {
  if (!entry || entries.some((item) => item.id === entry.id)) return entries.slice();
  const at = Math.max(0, Math.min(Number(index) || 0, entries.length));
  const out = entries.slice();
  out.splice(at, 0, entry);
  return out;
}

/** What stops a save, the server's limits checked first here: a list of
 * `{index, message}` (index −1 for the whole FAQ). */
export function entryProblems(entries) {
  const problems = [];
  if (entries.length > MAX_ENTRIES) problems.push({ index: -1, message: `A FAQ holds at most ${MAX_ENTRIES} questions.` });
  entries.forEach((entry, index) => {
    const question = String(entry.question ?? "").trim();
    if (!question) problems.push({ index, message: `Question ${index + 1} is empty.` });
    else if (question.length > MAX_QUESTION_LENGTH)
      problems.push({ index, message: `Question ${index + 1} is longer than ${MAX_QUESTION_LENGTH} characters.` });
    if (String(entry.answer ?? "").trim().length > MAX_ANSWER_LENGTH)
      problems.push({ index, message: `Answer ${index + 1} is longer than ${MAX_ANSWER_LENGTH} characters.` });
  });
  return problems;
}

function comparable(entries) {
  return JSON.stringify(
    (entries || []).map((entry) => [
      entry.id,
      String(entry.question ?? "").trim(),
      String(entry.answer ?? "").trim(),
      hasAnswer(entry) ? entry.source : "none",
      entry.t ?? null,
      entry.origin,
      entry.deskIds || [],
    ]),
  );
}

/** True when `current` differs from the entries last saved (or drafted). */
export function isDirty(baseline, current) {
  return comparable(baseline) !== comparable(current);
}

/** The desk updates that adding them would change: a desk question no entry
 * holds, or an answer for an entry that is still open. An entry with an
 * answer is never offered one. */
export function pendingUpdates(entries, updates) {
  return (updates || []).filter((update) => {
    const id = update.deskIds?.[0];
    const holding = entries.filter((entry) => (entry.deskIds || []).includes(id));
    if (!holding.length) return true;
    return hasAnswer(update) && holding.every((entry) => !hasAnswer(entry));
  });
}

/** `entries` with the pending desk updates merged in: an open entry with
 * the same desk id takes the desk's answer (its question stays as the user
 * wrote it); a desk question no entry holds is added at the end. An entry
 * with an answer is never changed. */
export function mergeUpdates(entries, updates) {
  let out = entries.map((entry) => ({ ...entry }));
  for (const update of pendingUpdates(out, updates)) {
    const id = update.deskIds?.[0];
    const index = out.findIndex((entry) => (entry.deskIds || []).includes(id));
    if (index === -1) {
      const { kind: _kind, ...entry } = update;
      const taken = new Set(out.map((item) => item.id));
      out = [...out, { ...entry, id: taken.has(entry.id) ? newEntry(out).id : entry.id, deskIds: [...(entry.deskIds || [])] }];
    } else if (!hasAnswer(out[index])) {
      out[index] = { ...out[index], answer: update.answer, source: update.source, t: update.t ?? null };
    }
  }
  return out;
}

/** "2 questions newly answered on the desk · 1 new question", or "". */
export function updatesLine(updates) {
  const answered = updates.filter((update) => hasAnswer(update)).length;
  const open = updates.length - answered;
  const parts = [];
  if (answered) parts.push(`${answered} ${answered === 1 ? "question" : "questions"} newly answered on the desk`);
  if (open) parts.push(`${open} new ${open === 1 ? "question" : "questions"} on the desk`);
  return parts.join(" · ");
}

function answerLines(entry) {
  const answer = String(entry.answer ?? "").trim();
  if (!answer) return ["Not answered yet."];
  const lines = answer.split("\n");
  if (entry.source === "talk" && replayTime(entry.t)) lines.push(`(${replayTime(entry.t)} in the replay)`);
  return lines;
}

/** One entry as Markdown, for its own Copy. */
export function entryMarkdown(entry) {
  return [`## ${String(entry.question ?? "").trim()}`, "", ...answerLines(entry)].join("\n");
}

/** The FAQ as Markdown, as the server's `faq_markdown` writes it (the
 * download): the title, then each question as a heading and its answer. */
export function toMarkdown(entries, title = "") {
  const name = String(title ?? "").trim();
  const blocks = [`# ${name ? `FAQ: ${name}` : "FAQ"}`, ...entries.map(entryMarkdown)];
  return `${blocks.join("\n\n")}\n`;
}

/** The download menu's choices: `{format, label, about}`. */
export const DOWNLOADS = [
  { format: "md", label: "Markdown (.md)", about: "For a replay page or a help article." },
  { format: "csv", label: "Spreadsheet (CSV)", about: "Question, answer, source and time in the replay." },
  { format: "pdf", label: "PDF", about: "To attach to the follow-up email." },
];
