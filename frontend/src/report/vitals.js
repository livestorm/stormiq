// The v1 Report's vitals and its "In short" card, worded from `record.measures`.
//
// The server computes every number (docs/product.md §13); this module only
// formats what a measure sends: `value` as a percentage or a duration, `num`
// and `den` as counts, `excluded.total` as "excludes your team (n)", and a
// time in minutes from the measure's own seconds. A hidden value stays
// hidden, and the card says why in words. Pure functions.

export const HIDDEN = new Set(["insufficient", "unresolved", "unavailable"]);

export function formatPercent(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "";
  return `${Math.round(value * 100)}%`;
}

/** "45 s", "2 min 15 s", "1 h 05 min" from seconds; "" when unknown. */
export function formatSeconds(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return "";
  const total = Math.round(seconds);
  if (total < 60) return `${total} s`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) {
    const rest = total % 60;
    return rest ? `${minutes} min ${rest} s` : `${minutes} min`;
  }
  const hours = Math.floor(minutes / 60);
  return `${hours} h ${String(minutes % 60).padStart(2, "0")} min`;
}

export function plural(count, one, many) {
  return `${count} ${count === 1 ? one : many}`;
}

/** The status word for a shown value: "measured" when ok, "estimated" when
 * the method is approximate, the inputs incomplete, or the value counts the
 * chat labels (a model's reading, `coverage.labels: "model"`); "" when
 * hidden. */
export function statusFor(measure) {
  const state = measure?.state;
  if (state === "ok") return measure.coverage?.labels === "model" ? "estimated" : "measured";
  if (state === "partial" || state === "approximation") return "estimated";
  return "";
}

/** Why a value is hidden, in words. */
export function hiddenReason(measure) {
  switch (measure?.state) {
    case "insufficient":
      return "Not enough data for a reliable number.";
    case "unresolved":
      return "Who wrote is unknown for this session.";
    case "unavailable": {
      const missing = measure?.coverage?.missing || [];
      if (missing.includes("labels")) return "Run the analysis to count the chat's questions.";
      return "This session's data doesn't have it.";
    }
    default:
      return "";
  }
}

export function excludedNote(measure) {
  const excluded = measure?.excluded;
  if (!excluded) return "";
  if (excluded.internal) return "Internal session: everyone counts.";
  return excluded.total > 0 ? `Excludes your team: ${excluded.total}.` : "";
}

export function partialNote(measure) {
  if (measure?.state === "partial") return "Estimated: the people list or the chat isn't complete yet.";
  if (measure?.state === "approximation") return "Estimated by method.";
  return "";
}

function join(...parts) {
  return parts.filter(Boolean).join(" ");
}

function card(id, measure, { icon, label, definition, value, sub, extra = "" }) {
  const hidden = !measure || HIDDEN.has(measure.state);
  return {
    id,
    icon,
    label,
    value: hidden ? "—" : value,
    sub: hidden ? hiddenReason(measure) || sub : sub,
    info: join(definition, extra, excludedNote(measure), partialNote(measure)),
    status: hidden ? "" : statusFor(measure),
    hidden,
  };
}

/** The five vitals, in order. Each card is `{id, icon, label, value, sub,
 * info, status, hidden}` for `StatCard`. */
export function vitalCards(measures = {}) {
  const showUp = measures["m.show_up_rate"];
  const room = measures["m.time_in_room"];
  const wrote = measures["m.wrote_rate"];
  const total = measures["m.questions_total"];
  const qa = measures["m.questions_qa"];
  const answer = measures["m.time_to_answer"];

  const cards = [];

  const livestorm = showUp?.livestorm;
  cards.push(
    card("show-up", showUp, {
      icon: "user-check",
      label: "Show-up rate",
      definition: "Audience attendees ÷ audience registrants.",
      value: formatPercent(showUp?.value),
      sub: showUp?.den != null ? `${showUp.num} of ${plural(showUp.den, "registrant", "registrants")}` : "",
      extra: livestorm?.value != null ? `Livestorm's dashboard shows ${formatPercent(livestorm.value)} (your team included).` : "",
    }),
  );

  const median = room?.coverage?.medianAttendanceS;
  const length = room?.coverage?.sessionLengthS;
  cards.push(
    card("time-in-room", room, {
      icon: "timer",
      label: "Time in the room",
      definition: "The median time an audience attendee watched, as a share of the session. Time watched, not when.",
      value: formatPercent(room?.value),
      sub: median != null && length ? `Median ${Math.round(median / 60)} of ${Math.round(length / 60)} min` : "",
      extra: median != null && length ? `Median ${formatSeconds(median)} of a ${formatSeconds(length)} session.` : "",
    }),
  );

  cards.push(
    card("wrote", wrote, {
      icon: "message-circle",
      label: "Wrote in chat or Q&A",
      definition: "Audience attendees who wrote at least once in the chat or the Q&A ÷ audience attendees.",
      value: formatPercent(wrote?.value),
      sub: wrote?.den != null && wrote.num != null ? `${wrote.num} of ${plural(wrote.den, "attendee", "attendees")}` : "",
    }),
  );

  // Questions: Q&A plus the chat's questions once the analysis labelled the
  // chat; until then, the Q&A alone, said as such.
  const totalShown = total && !HIDDEN.has(total.state);
  const rows = Object.fromEntries((total?.rows || []).map((row) => [row.key, row.questions]));
  const qaCount = qa && qa.state !== "unavailable" ? qa.den : null;
  if (totalShown) {
    cards.push(
      card("questions", total, {
        icon: "help-circle",
        label: "Questions",
        definition: "Questions asked in the Q&A, plus chat messages the analysis labelled as questions about the content. Questions about the session itself (a replay, the slides) are counted apart.",
        value: String(total.value),
        sub: `${rows.qa ?? 0} in Q&A · ${rows.chat ?? 0} in the chat`,
        extra: rows.chatLogistics ? `Plus ${plural(rows.chatLogistics, "question", "questions")} about the session itself.` : "",
      }),
    );
  } else if (qaCount != null) {
    // The Q&A's own count, which no gate hides, until the chat is labelled.
    cards.push({
      id: "questions",
      icon: "help-circle",
      label: "Questions",
      value: String(qaCount),
      sub: "In Q&A only, for now",
      info: "Questions asked in the Q&A. Run the analysis to add the questions asked in the chat.",
      status: "measured",
      hidden: false,
    });
  } else {
    cards.push(card("questions", total, { icon: "help-circle", label: "Questions", definition: "Questions asked in the Q&A and the chat.", value: "", sub: "" }));
  }

  const delay = answer && !HIDDEN.has(answer.state) ? formatSeconds(answer.value) : "";
  cards.push(
    card("answered", qa, {
      icon: "check",
      label: "Answered",
      definition: "Q&A questions answered in writing, marked answered aloud, or marked answered ÷ Q&A questions.",
      value: formatPercent(qa?.value),
      sub: qa?.den != null ? `${qa.num} of ${plural(qa.den, "question", "questions")}` : "",
      extra: delay ? `Median time to a written answer: ${delay}.` : "",
    }),
  );

  return cards;
}

/** The "In short" card: one sentence from the measures, and up to three
 * chips. `{ sentence, chips }`; the sentence is "" when there is too little
 * to say. */
export function inShort(measures = {}) {
  const attendees = measures["m.audience_attendees"];
  const registrants = measures["m.audience_registrants"];
  const wrote = measures["m.wrote_rate"];
  const qa = measures["m.questions_qa"];
  const total = measures["m.questions_total"];

  const parts = [];
  if (attendees?.value != null && registrants?.value) {
    parts.push(`${plural(attendees.value, "person", "people")} came out of ${registrants.value} registered`);
  } else if (attendees?.value != null) {
    parts.push(`${plural(attendees.value, "person", "people")} came`);
  }
  if (wrote && !HIDDEN.has(wrote.state) && wrote.num != null) {
    parts.push(`${wrote.num} of them wrote in the chat or the Q&A`);
  }
  const sentence = parts.length ? `${parts.join(", and ")}.` : "";

  const chips = [];
  const questions = total && !HIDDEN.has(total.state) ? total.value : qa?.den;
  if (questions != null) chips.push({ icon: "help-circle", text: plural(questions, "question", "questions") });
  const open = (qa?.rows || []).find((row) => row.key === "open")?.questions;
  if (open != null && qa?.state !== "unavailable") chips.push({ icon: "message-circle", text: `${open} still open in Q&A` });
  return { sentence, chips };
}
