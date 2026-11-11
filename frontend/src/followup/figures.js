// Before you share's words: pure functions over `GET /record/figures`
// (docs/api.md, "Before you share"). The server builds the checks, their
// questions and options; the page words what it sent.

import { clockLabel } from "../report/chartGeometry";

function count(value) {
  return Number.isFinite(Number(value)) ? Number(value) : 0;
}

/** The card's line: "2 figures said on stage to check before you share",
 * "All 2 figures checked", or "" without any check. */
export function figuresSummary(body) {
  const total = (body?.checks || []).length;
  if (!total) return "";
  const open = count(body?.unchecked);
  if (!open) return total === 1 ? "The figure is checked" : `All ${total} figures checked`;
  return `${open} ${open === 1 ? "figure" : "figures"} said on stage to check before you share`;
}

/** "5:18 · “pricing works per team”" for one moment a figure was said. */
export function momentLine(moment = {}) {
  const t = moment.t;
  const time = typeof t === "number" && Number.isFinite(t) && t >= 0 ? clockLabel(t) : "";
  return [time, `“${moment.quote || ""}”`].filter(Boolean).join(" · ");
}

/** The chosen option's label, or "" while the check is open. */
export function chosenLabel(check = {}) {
  if (!check.choice) return "";
  return (check.options || []).find((option) => option.value === check.choice)?.label || check.choice;
}

/** The quiet line on an AI output while figures are unchecked, or "". */
export function uncheckedNote(body) {
  const open = count(body?.unchecked);
  if (!open) return "";
  return `${open} ${open === 1 ? "figure" : "figures"} said on stage ${open === 1 ? "isn't" : "aren't"} checked yet: see Before you share, above.`;
}
