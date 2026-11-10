// The v1 Report's "The room" section, worded from `record.measures`: for now,
// the chat themes (`m.chat_themes`), where each row opens its messages.
//
// The server computes the counts, shares and gate (docs/product.md §13); this
// module labels the classes, formats the cells and says why a share is
// missing. The classes are a model's reading, so the status word is always
// "estimated" and every row keeps its message ids (`ids`) for the page to
// open. Pure functions.

import { HIDDEN, formatPercent, plural, statusFor } from "./vitals";

const EMPTY = "—";

// In the server's order (`record.chat_labels.LABEL_CLASSES`).
export const THEME_LABELS = {
  content: "Reactions to the content",
  question: "Questions",
  tech: "Sound, video and connection",
  logistics: "Logistics (replay, slides, timing)",
  social: "Greetings and thanks",
  laughter: "Laughter",
  off_topic: "Off topic",
};

let languageNames = null;
function languageName(code) {
  try {
    languageNames ??= new Intl.DisplayNames(["en"], { type: "language" });
    return languageNames.of(code) || code;
  } catch {
    return code;
  }
}

/** "English", or "English, French (2)": the most frequent language, then the
 * others with their counts; a message without words isn't a language. */
export function languagesLabel(languages = []) {
  const spoken = languages.filter((item) => item.language);
  if (!spoken.length) return EMPTY;
  const [first, ...rest] = spoken;
  return [languageName(first.language), ...rest.map((item) => `${languageName(item.language)} (${item.messages})`)].join(", ");
}

/** The status word of a measure that reads the chat labels: they are a
 * model's reading, so "estimated" whenever the value shows (`statusFor`
 * reads `coverage.labels`). */
export function labelStatus(measure) {
  if (!measure || HIDDEN.has(measure.state)) return "";
  return statusFor(measure);
}

const THEMES_SAID =
  "Every audience chat message, sorted into one theme by the analysis (AI). The share is of the audience's messages, shown from 30 messages. Open a row to read its messages and check the sorting.";

/** The chat themes table: `{rows, empty, notes, status, info, total}`. A row
 * is `{key, label, messages, share, bar, languages, ids}`; a theme with no
 * message is left out and named in a note. Null when the measure is
 * missing; an unavailable measure has no rows and says why. */
export function chatThemes(measure) {
  if (!measure) return null;
  if (measure.state === "unavailable") {
    return { rows: [], empty: "Run the analysis to sort the chat by theme.", notes: [], status: "", info: THEMES_SAID, total: "" };
  }
  const rows = [];
  const none = [];
  for (const row of measure.rows || []) {
    const label = THEME_LABELS[row.key] || row.key;
    if (!row.messages) {
      none.push(label.toLowerCase());
      continue;
    }
    rows.push({
      key: row.key,
      label,
      messages: String(row.messages),
      share: formatPercent(row.share) || EMPTY,
      // The bar's width: the server's share as sent, null when hidden.
      bar: typeof row.share === "number" && Number.isFinite(row.share) ? Math.max(0, Math.min(1, row.share)) * 100 : null,
      languages: languagesLabel(row.languages),
      ids: row.ids || [],
    });
  }
  const notes = [];
  if (measure.state === "insufficient" && rows.length) notes.push("Fewer than 30 audience messages: the counts are shown, not the shares.");
  if (none.length && rows.length) notes.push(`No message about: ${none.join(", ")}.`);
  const team = measure.coverage?.teamMessages;
  let excluded = "";
  if (measure.excluded?.internal) excluded = "Internal session: everyone's messages count.";
  else if (team) excluded = `${plural(team, "message", "messages")} from your team left out.`;
  return {
    rows,
    empty: rows.length ? "" : "The audience wrote nothing in the chat.",
    notes,
    status: labelStatus(measure),
    info: [THEMES_SAID, excluded].filter(Boolean).join(" "),
    total: measure.den ? plural(measure.den, "audience message", "audience messages") : "",
  };
}

/** A theme row's name for screen readers, as one sentence: "Reactions to
 * the content: 492 messages, 82% of the chat, English". A hidden share or
 * an unknown language is left out. */
export function themeRowName(row) {
  const parts = [`${row.messages} ${row.messages === "1" ? "message" : "messages"}`];
  if (row.share !== EMPTY) parts.push(`${row.share} of the chat`);
  if (row.languages !== EMPTY) parts.push(row.languages);
  return `${row.label}: ${parts.join(", ")}`;
}

/** The collapsed card's one line and key chip. The line names the largest
 * theme only when shares show (the first on a tie, in the server's order). */
export function roomSummary(measures = {}) {
  const measure = measures["m.chat_themes"];
  const themes = chatThemes(measure);
  let takeaway = "What the chat was about.";
  if (themes?.rows.length && !HIDDEN.has(measure.state)) {
    const top = themes.rows.reduce((best, row) => (Number(row.messages) > Number(best.messages) ? row : best));
    takeaway = `Most of the chat: ${top.label.toLowerCase()} (${top.share}).`;
  }
  return { takeaway, chip: themes?.total || "" };
}
