// The v1 Report's "Who spoke up", worded from `GET /record/voices`: the
// audience members who wrote, the most active first, each with a short
// name, their company, how much they wrote and one of their messages; then
// how spread out the chat was (`m.chat_concentration`, worded by
// `audience.js`). The server counts and ranks; this module only words.
// Pure functions.

import { clockLabel } from "./chartGeometry";
import { formatPercent, plural } from "./vitals";

const UNNAMED = "An attendee";

function counts(messages, questions) {
  const parts = [];
  if (messages) parts.push(plural(messages, "message", "messages"));
  if (questions) parts.push(plural(questions, "question", "questions"));
  return parts.join(" · ");
}

function when(t) {
  if (typeof t !== "number" || !Number.isFinite(t)) return "";
  return t < 0 ? "Before the start" : clockLabel(t);
}

/** `{lead, rows, others, unnamed, empty}`, or null before the list loads.
 * A row is `{key, name, named, company, counts, share, width, quote, time}`:
 * `width` is the share as a CSS percentage for its bar. */
export function voicesView(body) {
  if (!body || !body.total) return null;
  const total = body.total;
  if (!total.people) return { lead: "", rows: [], others: "", unnamed: "", empty: "Nobody in the audience wrote in the chat or the Q&A." };
  const rows = (body.people || []).map((person, index) => ({
    key: `${index}-${person.name || "unnamed"}`,
    name: person.name || UNNAMED,
    named: Boolean(person.name),
    company: person.company || "",
    counts: counts(person.messages, person.questions),
    share: formatPercent(person.share),
    width: `${Math.max(2, Math.round((person.share || 0) * 100))}%`,
    quote: person.sample?.text || "",
    time: when(person.sample?.t),
  }));
  const others = body.others?.people
    ? `And ${plural(body.others.people, "more person", "more people")}: ${counts(body.others.messages, body.others.questions)}.`
    : "";
  const what = counts(total.messages, total.questions) || "nothing";
  return {
    lead: `${plural(total.people, "person", "people")} in the audience wrote: ${what}.${body.internal ? " Everyone counts on an internal session." : ""}`,
    rows,
    others,
    unnamed: rows.some((row) => !row.named)
      ? "“An attendee” is someone whose messages couldn't be matched to a registrant, so their name isn't known. Fetching the chat again can fix it."
      : "",
    empty: "",
  };
}
