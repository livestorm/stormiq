// The v1 Report's audience section, worded from `record.measures`: the room
// map, chat concentration, channels, segments and show-up by lead time.
//
// The server computes every number and every gate (docs/product.md §13); this
// module only labels rows and formats what they send. A null row value stays
// empty ("—" on screen), a row's own `state` says why, and an `(other)` or
// `direct_or_unknown` row is a label, never a channel to compare. Pure
// functions.

import { HIDDEN, excludedNote, formatPercent, formatSeconds, hiddenReason, partialNote, plural, statusFor } from "./vitals";

const OTHER = "(other)";
const DIRECT_OR_UNKNOWN = "direct_or_unknown";
const EMPTY = "—";

function join(...parts) {
  return parts.filter(Boolean).join(" ");
}

function count(value) {
  return typeof value === "number" && Number.isFinite(value) ? String(value) : EMPTY;
}

function percent(value) {
  return formatPercent(value) || EMPTY;
}

/** A shown measure's status word, or "" when hidden. */
function status(measure) {
  return measure && !HIDDEN.has(measure.state) ? statusFor(measure) : "";
}

function info(measure, definition, extra = "") {
  return join(definition, extra, excludedNote(measure), partialNote(measure));
}

/** A table's duration: seconds under a minute, else whole minutes ("52 min",
 * "1 h 08 min"), from the row's own seconds. */
export function formatMinutes(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return EMPTY;
  if (seconds < 60) return formatSeconds(seconds);
  return formatSeconds(Math.round(seconds / 60) * 60);
}

// ── Room map ────────────────────────────────────────────────────────────────

const ROOM_CELLS = {
  stayed_wrote: { label: "Stayed and wrote", says: "stayed to the end and wrote" },
  stayed_quiet: { label: "Stayed without writing", says: "stayed to the end without writing" },
  left_wrote: { label: "Wrote, then left early", says: "wrote, then left before the end" },
  left_quiet: { label: "Left early without writing", says: "left before the end without writing" },
};

/** The four tiles in the server's order, `{key, label, people, share,
 * emphasis}`, and a sentence about the largest cell (the first on a tie),
 * which is the emphasised one. Counts show unless the authors are
 * unresolved; shares and the sentence only when the measure isn't hidden. */
export function roomMap(measure) {
  if (!measure) return null;
  const hidden = HIDDEN.has(measure.state);
  const rows = measure.rows || [];
  let largest = null;
  if (!hidden) {
    for (const row of rows) {
      if (typeof row.people === "number" && (largest == null || row.people > largest.people)) largest = row;
    }
  }
  const tiles = rows.map((row) => ({
    key: row.key,
    label: ROOM_CELLS[row.key]?.label || row.key,
    people: count(row.people),
    share: hidden ? "" : formatPercent(row.share),
    emphasis: row === largest,
  }));
  let sentence = "";
  if (largest && measure.den && ROOM_CELLS[largest.key]) {
    sentence = `${largest.people} of ${plural(measure.den, "attendee", "attendees")} ${ROOM_CELLS[largest.key].says}.`;
  }
  let note = "";
  if (measure.state === "unresolved") note = hiddenReason(measure);
  else if (hidden) note = "Too few attendees for shares: the counts are shown.";
  const unknown = measure.coverage?.durationUnknown;
  return {
    tiles,
    sentence,
    note,
    status: status(measure),
    info: info(
      measure,
      "Audience attendees in four groups: stayed (at least 75% of the session) or left earlier, and wrote in the chat or the Q&A or didn't. Time watched, not when.",
      unknown ? `${plural(unknown, "attendee", "attendees")} without an attendance time left out.` : "",
    ),
  };
}

// ── Chat concentration ──────────────────────────────────────────────────────

/** "3 people wrote half the chat", from k and the top row. */
export function concentration(measure) {
  if (!measure) return null;
  const hidden = HIDDEN.has(measure.state);
  const top = (measure.rows || []).find((row) => row.key === "top");
  const authors = measure.coverage?.authors;
  let value = EMPTY;
  let sentence = "";
  let note = "";
  if (!hidden && measure.value != null) {
    value = plural(measure.value, "person", "people");
    sentence = `wrote ${formatPercent(top?.share) || "half"} of the ${plural(measure.den, "audience message", "audience messages")}`;
    if (authors != null) sentence += `, out of ${plural(authors, "audience member", "audience members")} who wrote in the chat`;
    sentence += ".";
  } else if (measure.state === "insufficient") {
    note = `${plural(measure.den ?? 0, "audience message", "audience messages")}: too few to say how spread out the chat was.`;
  } else {
    note = hiddenReason(measure);
  }
  return {
    value,
    sentence,
    note,
    status: status(measure),
    info: info(
      measure,
      "The fewest audience members whose chat messages make at least half of the audience messages, the most active first.",
    ),
  };
}

// ── Channels and segments ───────────────────────────────────────────────────

/** A channel row's label: the UTM source or referrer as sent, a
 * registration type in words, "Other" and "No source" for the two folds. */
export function channelLabel(row) {
  if (row.key === OTHER) return `Other (${plural(row.channels ?? 0, "channel", "channels")})`;
  if (row.key === DIRECT_OR_UNKNOWN) return "No source";
  if (typeof row.key === "string" && row.key.startsWith("type:")) return `Registration type: ${row.key.slice(5)}`;
  return row.key;
}

export function segmentLabel(row) {
  if (row.kind === "other") return `Other (${plural(row.values ?? 0, "answer", "answers")})`;
  return row.label || row.value || row.key;
}

/** One table row of a channel or segment, every cell formatted. */
function qualityRow(row, label) {
  return {
    key: row.key,
    label,
    fold: row.kind === "other" || row.kind === "none",
    registrants: count(row.registrants),
    share: percent(row.share),
    attendees: count(row.attendees),
    showUp: percent(row.showUpRate),
    wrote: percent(row.wroteRate),
    medianTime: formatMinutes(row.medianAttendanceS),
    gated: row.state === "insufficient",
  };
}

function authorsUnknown(measure) {
  const coverage = measure?.coverage || {};
  return coverage.chatAuthors === "unresolved" || coverage.questionAuthors === "unresolved";
}

const QUALITY_COLUMNS_SAID =
  "Show-up: attendees ÷ registrants. Wrote: attendees who wrote in the chat or the Q&A ÷ attendees. Median time: how long the middle attendee watched.";

function qualityNotes(measure, rows) {
  const notes = [];
  if (!HIDDEN.has(measure.state) && rows.some((row) => row.gated)) notes.push("— Too few people in this row for a rate.");
  if (authorsUnknown(measure)) notes.push("Who wrote is unknown for this session, so that column is empty.");
  return notes;
}

/** The channel table: rows in the server's order and the notes under it. */
export function channelTable(measure) {
  if (!measure) return null;
  const coverage = measure.coverage || {};
  const rows = (measure.rows || []).map((row) => qualityRow(row, channelLabel(row)));
  let empty = "";
  if (!rows.length) {
    empty = coverage.withSource
      ? "Not enough registrants per channel to compare."
      : "No registrant has a source (a UTM, a referrer or a registration type), so there are no channels.";
  }
  const notes = qualityNotes(measure, rows);
  if (rows.length && HIDDEN.has(measure.state) && coverage.registrants != null) {
    notes.unshift(
      `Too few to compare channels: ${coverage.withSource ?? 0} of ${plural(coverage.registrants, "registrant", "registrants")} have a source. The counts are shown.`,
    );
  }
  return {
    rows,
    empty,
    notes,
    status: status(measure),
    info: info(
      measure,
      "Where audience registrants came from: the UTM source, else the referring site, else the registration type. Channels under 20 registrants fold into Other.",
      QUALITY_COLUMNS_SAID,
    ),
  };
}

/** One table per select field of the registration form, in the server's
 * order, named from `coverage.fields`. */
export function segmentTables(measure) {
  if (!measure) return null;
  const fields = measure.coverage?.fields || [];
  const byField = new Map();
  for (const row of measure.rows || []) {
    if (!byField.has(row.field)) byField.set(row.field, []);
    byField.get(row.field).push(row);
  }
  const tables = [...byField.entries()].map(([field, rows]) => {
    const meta = fields.find((item) => item.field === field) || {};
    const formatted = rows.map((row) => qualityRow(row, segmentLabel(row)));
    return {
      field,
      name: meta.name || field,
      detail: join(
        meta.answered != null ? `${meta.answered} answered.` : "",
        meta.type === "multiple_select" ? "Several answers allowed, so the shares can add up to more than 100%." : "",
      ),
      rows: formatted,
      notes: qualityNotes(measure, formatted),
    };
  });
  let empty = "";
  let note = "";
  if (!tables.length) empty = "The registration form has no question with set answers to compare.";
  else if (HIDDEN.has(measure.state)) note = "Too few registrants per answer to compare: the counts are shown.";
  return {
    tables,
    empty,
    note,
    status: status(measure),
    info: info(
      measure,
      "Audience registrants by their answer to each multiple-choice question of the registration form. Answers under 10 registrants fold into Other.",
      QUALITY_COLUMNS_SAID,
    ),
  };
}

// ── Show-up by lead time ────────────────────────────────────────────────────

const LEAD = {
  same_day: { label: "Under 24 hours", low: 0, high: 0 },
  "1-3": { label: "1–3 days", low: 1, high: 3 },
  "4-7": { label: "4–7 days", low: 4, high: 7 },
  "8-14": { label: "8–14 days", low: 8, high: 14 },
  "15+": { label: "15 days or more", low: 15, high: null },
  after_start: { label: "After the start", low: null, high: null },
};

/** A lead-time row's label; a merged row reads from its first bucket's start
 * to its last bucket's end. */
export function leadLabel(row) {
  const buckets = row.buckets || [row.key];
  if (buckets.length === 1) return LEAD[buckets[0]]?.label || row.key;
  const first = LEAD[buckets[0]];
  const last = LEAD[buckets[buckets.length - 1]];
  if (!first || !last || first.low == null) return row.key;
  if (last.high == null) return first.low === 0 ? "Any time before" : `${first.low} days or more`;
  return first.low === 0 ? `Under ${last.high + 1} days` : `${first.low}–${last.high} days`;
}

export function leadTable(measure) {
  if (!measure) return null;
  const hidden = HIDDEN.has(measure.state);
  const rows = (measure.rows || []).map((row) => ({
    key: row.key,
    label: leadLabel(row),
    registrants: count(row.registrants),
    share: percent(row.share),
    attendees: count(row.attendees),
    showUp: percent(row.showUpRate),
    gated: row.state === "insufficient",
  }));
  const notes = [];
  if (hidden && rows.length) notes.push("Too few registrants with a registration time for rates: the counts are shown.");
  else if (rows.some((row) => row.gated)) notes.push("— Too few people in this row for a rate.");
  const unknown = measure.coverage?.leadUnknown;
  return {
    rows,
    empty: rows.length ? "" : "No registrant has a registration time.",
    notes,
    status: status(measure),
    info: info(
      measure,
      "Show-up rate by how long before the start people registered, in 24-hour periods. Groups under 20 registrants merge with a neighbour.",
      unknown ? `${plural(unknown, "registrant", "registrants")} without a registration time left out.` : "",
    ),
  };
}

// ── The section's header ────────────────────────────────────────────────────

/** The collapsed card's one line and key chip. */
export function audienceSummary(measures = {}) {
  const room = roomMap(measures["m.room_map"]);
  const chat = concentration(measures["m.chat_concentration"]);
  return {
    takeaway: room?.sentence || "Who stayed, who wrote, and where the audience came from.",
    chip: chat?.sentence ? `Half the chat: ${chat.value}` : "",
  };
}

/** The session's own show-up rate, as the tables' reference. */
export function sessionShowUp(measures = {}) {
  const showUp = measures["m.show_up_rate"];
  return showUp && !HIDDEN.has(showUp.state) ? formatPercent(showUp.value) : "";
}
