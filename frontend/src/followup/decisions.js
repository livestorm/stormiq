// Follow-up's "Decisions and commitments" as cards: the words for what
// `GET /api/sessions/{id}/decisions` sends (`api_logic.get_session_decisions`).
// Pure functions. The server names every speaker, from the names saved on the
// Transcript tab when the list is read, so a rename shows here at once; the
// page never words a name or a time of its own beyond formatting seconds.

// Each kind as a section, in the order the server's Markdown lists them.
// A section's title is part of the output, so it follows the output's language.
export const KINDS = [
  { kind: "decision", icon: "check", word: "Decision", titles: { English: "Decisions", French: "Décisions" } },
  { kind: "commitment", icon: "user-check", word: "Commitment", titles: { English: "Commitments", French: "Engagements" } },
];

const NONE = {
  English: "No decisions or commitments were stated on stage.",
  French: "Aucune décision ni aucun engagement n'a été annoncé sur scène.",
};

export const OLD_LIST_NOTE =
  "Written before speaker names could update: a speaker renamed on the Transcript tab keeps their old name in this list.";
export const RECORDING_CLOCK_NOTE = "Times are on the recording's clock: the session's clock couldn't be read against it.";

/** "4:05", "1:02:09" from seconds; "" when unknown. */
export function clockText(seconds) {
  const value = Number(seconds);
  if (seconds === null || seconds === undefined || seconds === "" || !Number.isFinite(value)) return "";
  const total = Math.max(0, Math.round(value));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = String(total % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${secs}` : `${minutes}:${secs}`;
}

/** Up to two letters for an avatar: "Nora Vale" → "NV", "Speaker 2" → "S2",
 * "the product team" → "TT" (first and last words). "?" without letters. */
export function initials(name) {
  const words = String(name || "")
    .trim()
    .split(/\s+/)
    .map((word) => word.replace(/^[^\p{L}\p{N}]+/u, ""))
    .filter(Boolean);
  if (!words.length) return "?";
  const first = words[0][0];
  const last = words.length > 1 ? words[words.length - 1][0] : "";
  return `${first}${last}`.toUpperCase();
}

function text(value) {
  return String(value ?? "").trim();
}

function card(item, index, clock) {
  const at = clockText(item.at);
  const owner = item.owner && text(item.owner.name) ? item.owner : null;
  const saidBy = item.saidBy && text(item.saidBy.name) ? text(item.saidBy.name) : "";
  return {
    key: `${item.kind}-${index}`,
    text: text(item.text).replace(/\.+$/, "") + ".",
    owner: owner
      ? {
          name: text(owner.name),
          initials: initials(owner.name),
          // A speaker (named from the Transcript tab) or the talk's own words ("the product team").
          speaker: Boolean(owner.speaker),
          label: `Owner: ${text(owner.name)}`,
        }
      : null,
    due: text(item.due) ? { text: text(item.due), label: `Due: ${text(item.due)}` } : null,
    at: at
      ? { text: at, label: clock === "session" ? `Said at ${at} on the session clock` : `Said at ${at} in the recording` }
      : null,
    quote: text(item.quote),
    saidBy,
    // "Nora Vale at 4:05", or who alone, for the quote's line.
    attribution: [saidBy || "A speaker", at ? `at ${at}` : ""].filter(Boolean).join(" "),
  };
}

/** The cards for the server's body, in `language` ("English" or "French"):
 * `{saved, note, sections, empty, emptyText, markdown}`. `saved` is "items"
 * (cards), "markdown" (a list saved before names could update: render
 * `markdown` with `note`), or "none". `sections` hold only kinds with items,
 * each `{kind, title, word, icon, count, items}`. */
export function decisionView(body, language = "English") {
  const lang = language === "French" ? "French" : "English";
  const saved = body?.saved === "items" || body?.saved === "markdown" ? body.saved : "none";
  const markdown = text(body?.markdown);
  if (saved !== "items") {
    return { saved, note: saved === "markdown" ? OLD_LIST_NOTE : "", sections: [], empty: false, emptyText: "", markdown };
  }
  const clock = body.clock === "session" ? "session" : "recording";
  const items = Array.isArray(body.items) ? body.items : [];
  const sections = KINDS.map((meta) => {
    const own = items.filter((item) => item?.kind === meta.kind && text(item.text));
    return {
      kind: meta.kind,
      title: meta.titles[lang],
      word: meta.word,
      icon: meta.icon,
      count: own.length,
      items: own.map((item, index) => card(item, index, clock)),
    };
  }).filter((section) => section.count);
  return {
    saved,
    note: sections.length && clock !== "session" ? RECORDING_CLOCK_NOTE : "",
    sections,
    empty: !sections.length,
    emptyText: NONE[lang],
    markdown,
  };
}

/** The file name for the Markdown download. */
export function decisionsFileName(sessionId, language) {
  const safe = String(sessionId || "session").replace(/[^A-Za-z0-9_-]+/g, "-");
  return `stormiq-decisions-${safe}${language === "French" ? "-fr" : ""}.md`;
}
