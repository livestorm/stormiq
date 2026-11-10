// The v1 Report's "Method and data quality" card, worded from the record:
// what the numbers rest on (the transcript's health, the clock, the people
// list, who wrote, the chat labels) and "Not shown, and why": every number
// the page hides, with the reason the server gives.
//
// The server decides every state, gate and threshold (docs/product.md §13);
// a hidden measure's `gate` says which gate hid it and what the session had
// (`{field, minimum, actual}`), so this module only puts that in words. Pure
// functions.

import { HIDDEN, formatPercent, formatSeconds, plural } from "./vitals";

const EMPTY = "—";

function number(value, places = 2) {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(places) : "";
}

// ── What the numbers rest on ────────────────────────────────────────────────

const HEALTH = {
  ok: { word: "Good", tone: "good" },
  degraded: { word: "Usable, with care", tone: "fair" },
  poor: { word: "Poor", tone: "low" },
  unavailable: { word: "None yet", tone: "neutral" },
};

const HEALTH_REASONS = {
  no_speech: "The transcript has no speech.",
  low_confidence: "Many words were heard with low confidence.",
  unclear_words: "A large share of the words are unclear.",
  no_word_data: "The transcript gives no confidence for its words.",
  little_speech: "Speech covers little of the recording.",
  no_speakers: "Some speech has no speaker.",
  split_risk: "One speaker may have been split into several.",
  language_mismatch: "The spoken language isn't the event's.",
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

/** The transcript row: `{key, label, value, details, tone}`. */
export function transcriptRow(health) {
  const state = health?.state || "unavailable";
  const known = HEALTH[state] || HEALTH.unavailable;
  const details = [];
  if (state === "unavailable") {
    details.push("Fetch the transcript to read speech against the chat.");
  } else {
    const words = health.words || {};
    if (words.meanConfidence != null) {
      const low = words.lowConfidenceShare;
      const share = low > 0 && low < 0.005 ? "under 1%" : formatPercent(low);
      const unclear = share ? `; ${share} of words unclear` : "";
      details.push(`Mean word confidence ${number(words.meanConfidence)}${unclear}.`);
    }
    const share = health.coverage?.shareOfAudio;
    if (share != null) details.push(`Speech covers ${formatPercent(share)} of the recording.`);
    const speakers = health.speakers || {};
    if (speakers.substantial != null) {
      details.push(
        speakers.expected != null
          ? `Speakers: ${speakers.substantial} found, ${speakers.expected} expected.`
          : `Speakers: ${speakers.substantial} found.`,
      );
    }
    const language = health.language || {};
    if (language.detected) {
      details.push(
        language.expected && language.match === false
          ? `Spoken: ${languageName(language.detected)}; the event is set to ${languageName(language.expected)}.`
          : `Spoken: ${languageName(language.detected)}.`,
      );
    }
    for (const reason of health.reasons || []) {
      if (HEALTH_REASONS[reason]) details.push(HEALTH_REASONS[reason]);
    }
  }
  return { key: "transcript", label: "Transcript", value: known.word, details, tone: known.tone };
}

const CLOCK_REASONS = {
  no_transcript: "there is no transcript",
  no_clock: "the session has no start time",
  no_anchors: "no moment ties the recording to the chat",
  few_anchors: "fewer than two moments agree",
  weak_agreement: "the moments don't agree well",
  wide_range: "the moments leave a wide margin",
  one_direction: "the moments only bound one side",
  duration_gap: "the recording and the session differ in length",
  duration_unknown: "the recording's length is unknown",
  anchors_disagree: "the moments disagree",
  offset_not_zero: "the recording doesn't start with the session",
  drift: "the recording drifts against the chat",
};

function clockReasons(reasons = []) {
  const words = reasons.map((reason) => CLOCK_REASONS[reason]).filter(Boolean);
  if (!words.length) return "";
  const text = words.join("; ");
  return `Why: ${text}.`;
}

/** The clock row, from `meta.clock`: whether speech lines up with the chat,
 * and how precisely ("moment times ±5.7 s" from the server's margin). */
export function clockRow(clock = {}) {
  const alignment = clock.alignment || {};
  const details = [];
  let value = "Unknown";
  let tone = "neutral";
  if (alignment.status === "verified") {
    value = "Verified";
    tone = "good";
    const margin = typeof alignment.plusminusS === "number" ? ` Moment times ±${Math.round(alignment.plusminusS)} s.` : "";
    details.push(`Speech and chat line up on ${plural(alignment.nAgree ?? 0, "moment", "moments")}.${margin}`);
  } else if (alignment.status === "assumed") {
    value = "Assumed";
    tone = "fair";
    details.push("The recording is taken to start with the session: times hold to the minute, not the second.");
  } else if (alignment.status === "misaligned") {
    value = "Doesn't line up";
    tone = "low";
    details.push("Speech is left out of anything read against the chat.");
  } else if (alignment.status === "unavailable") {
    value = "Chat only";
    details.push("Only the chat and the questions are on the clock.");
  }
  const why = clockReasons(alignment.reasons);
  if (why) details.push(why);
  if (typeof clock.lateStartS === "number" && clock.lateStartS > 0) {
    details.push(`The session started ${formatSeconds(clock.lateStartS)} after its planned time.`);
  }
  return { key: "clock", label: "Clock", value, details, tone };
}

/** The people list row, from `record.roster` and `m.roster_coverage`. */
export function peopleRow(roster = {}, coverage) {
  const details = [];
  const people = roster.peopleCount ?? 0;
  const complete = roster.coverage === "complete";
  if (roster.source === "paginated" && roster.recordCount != null) {
    details.push(`${people} of the ${roster.recordCount} people Livestorm counts.`);
  } else if (roster.source === "paginated") {
    details.push(`${plural(people, "person", "people")}, from Livestorm's full list.`);
  } else {
    details.push(`${plural(people, "person", "people")}, from what Livestorm sent with the session. The full list hasn't been read yet.`);
  }
  const share = coverage && !HIDDEN.has(coverage.state) ? formatPercent(coverage.value) : "";
  if (share && !complete) details.push(`Coverage: ${share}.`);
  const sync = roster.sync || {};
  if (sync.active) details.push("Reading the full list from Livestorm now.");
  else if (sync.status === "error") details.push("The last read of the full list failed.");
  else if (roster.refreshDue) details.push("A fresh read of the list is due.");
  if (roster.internal) details.push("Internal session: everyone counts as audience.");
  return { key: "people", label: "People list", value: complete ? "Complete" : "Partial", details, tone: complete ? "good" : "fair" };
}

/** Who wrote, from `m.author_resolution`. Null when the measure is missing
 * or there was nobody to match. */
export function authorsRow(measure) {
  if (!measure || measure.state === "unavailable") return null;
  if (measure.state === "unresolved") {
    return {
      key: "authors",
      label: "Who wrote",
      value: "Unknown",
      tone: "low",
      details: ["The chat was saved without its authors, so who wrote is unknown. Numbers that need it are hidden."],
    };
  }
  if (measure.state === "insufficient") return null;
  return {
    key: "authors",
    label: "Who wrote",
    value: `${formatPercent(measure.value) || EMPTY} matched`,
    tone: measure.state === "ok" ? "good" : "fair",
    details: [
      measure.den != null ? `${measure.num} of ${plural(measure.den, "author", "authors")} matched to the people list.` : "",
      measure.state === "partial" ? "Estimated: the people list isn't complete, or a message's author wasn't found." : "",
    ].filter(Boolean),
  };
}

/** The chat labels row, from `record.chatLabels`. */
export function labelsRow(chatLabels = {}) {
  const state = chatLabels.state || "none";
  if (state === "ready") {
    const when = chatLabels.labelledAt ? new Date(chatLabels.labelledAt) : null;
    const date =
      when && !Number.isNaN(when.getTime())
        ? new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }).format(when)
        : "";
    const count = chatLabels.messages != null ? plural(chatLabels.messages, "message", "messages") : "The chat";
    return {
      key: "labels",
      label: "Chat sorting",
      value: "Done (AI)",
      tone: "good",
      details: [
        `${count} sorted${date ? ` on ${date}` : ""}.`,
        "A model's reading: every count from it reads “Estimated” and opens its messages.",
      ],
    };
  }
  if (state === "stale") {
    return { key: "labels", label: "Chat sorting", value: "Out of date", tone: "fair", details: ["The chat changed since: run the analysis again."] };
  }
  return { key: "labels", label: "Chat sorting", value: "Not yet", tone: "neutral", details: ["Run the analysis to sort the chat and count its questions."] };
}

/** Every row of "What the numbers rest on", in order, from the route's
 * whole response (`{meta, record}`). */
export function methodRows(body) {
  const record = body?.record || {};
  return [
    transcriptRow(record.transcriptHealth),
    clockRow(body?.meta?.clock),
    peopleRow(record.roster, record.measures?.["m.roster_coverage"]),
    authorsRow(record.measures?.["m.author_resolution"]),
    labelsRow(record.chatLabels),
  ].filter(Boolean);
}

// ── Not shown, and why ──────────────────────────────────────────────────────

// The measures the page shows, in page order, with their names and how to
// say each gate's quantity: `noun` for a count, `share` for a ratio,
// `seconds` for a length.
const SHOWN = [
  ["m.show_up_rate", "Show-up rate", { den: { noun: ["registrant", "registrants"] } }],
  ["m.time_in_room", "Time in the room", { attendees: { noun: ["attendee", "attendees"] }, session_length_s: { seconds: true } }],
  ["m.wrote_rate", "Wrote in chat or Q&A", { den: { noun: ["attendee", "attendees"] } }],
  ["m.questions_qa", "Questions answered", { den: { noun: ["question", "questions"] } }],
  ["m.time_to_answer", "Time to a written answer", { den: { noun: ["timed written answer", "timed written answers"] } }],
  ["m.questions_total", "Questions in the chat", {}],
  ["m.chat_themes", "Chat theme shares", { den: { noun: ["audience message", "audience messages"] } }],
  ["m.room_map", "Who stayed, who wrote: shares", { den: { noun: ["attendee", "attendees"] }, session_length_s: { seconds: true } }],
  ["m.chat_concentration", "How spread out the chat was", { den: { noun: ["audience message", "audience messages"] } }],
  ["m.channel_quality", "Channel rates", { sourced_share: { share: "of registrants have a source" }, channels: { noun: ["channel", "channels"] } }],
  ["m.segment_quality", "Registration answer rates", { fields: { noun: ["multiple-choice question", "multiple-choice questions"] } }],
  ["m.lead_time_show_up", "Show-up by registration time", { den: { noun: ["registrant with a registration time", "registrants with a registration time"] } }],
];

const MISSING = {
  labels: "Run the analysis to sort the chat.",
  chat: "The chat wasn't fetched.",
  questions: "The questions weren't fetched.",
  transcript: "There is no transcript yet.",
  roster: "The people list wasn't fetched.",
  session: "The session's details weren't fetched.",
};

/** Why a gate hid a measure, from the server's `gate`: "4 attendees; shown
 * from 10." */
export function gateReason(gate, says = {}) {
  if (!gate) return "Not enough data for a reliable number.";
  const how = says[gate.field] || {};
  const actual = gate.actual ?? 0;
  if (how.share) return `${formatPercent(actual)} ${how.share}; shown from ${formatPercent(gate.minimum)}.`;
  if (how.seconds) return `The session lasted ${formatSeconds(actual) || "0 s"}; shown from ${formatSeconds(gate.minimum)}.`;
  if (how.noun) {
    const had = actual === 0 ? `No ${how.noun[1]}` : plural(actual, how.noun[0], how.noun[1]);
    return `${had}; shown from ${gate.minimum}.`;
  }
  return "Not enough data for a reliable number.";
}

/** Why a measure is hidden, in words, or "" when it shows. */
export function hiddenWhy(measure, says = {}) {
  if (!measure || !HIDDEN.has(measure.state)) return "";
  if (measure.state === "unresolved") return "Who wrote is unknown for this session.";
  if (measure.state === "unavailable") {
    const missing = measure.coverage?.missing || [];
    return missing.map((source) => MISSING[source]).find(Boolean) || "This session's data doesn't have it.";
  }
  return gateReason(measure.gate, says);
}

/** `[{id, label, why}]`: every number the page would show that is hidden,
 * in page order. */
export function notShown(measures = {}) {
  const rows = [];
  for (const [id, label, says] of SHOWN) {
    const why = hiddenWhy(measures[id], says);
    if (why) rows.push({ id, label, why });
  }
  return rows;
}

/** The collapsed card's one line and key chip. */
export function methodSummary(body) {
  const health = body?.record?.transcriptHealth?.state;
  const clock = body?.meta?.clock?.alignment?.status;
  const hidden = notShown(body?.record?.measures || {});
  const parts = [`Transcript: ${(HEALTH[health] || HEALTH.unavailable).word.toLowerCase()}`];
  if (clock === "verified") parts.push("clock verified");
  else if (clock === "assumed") parts.push("clock assumed");
  else if (clock === "misaligned") parts.push("clock doesn't line up");
  return { takeaway: `${parts.join(", ")}.`, chip: hidden.length ? `${hidden.length} not shown` : "" };
}
