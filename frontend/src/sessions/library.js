// The Sessions library's extra cells for one session, from `GET
// /api/sessions/{id}/record/library` (`record/library.py`): its shape, the
// share who wrote, the questions and how many are still open, and the lead
// finding in one line, worded with the Report's own templates. Pure
// functions: the numbers are the server's, as the Report shows them.

import { foundFinding } from "../report/findings";

const EMPTY = "—";

function finite(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** The sparkline's SVG paths in a `width` × `height` box: `{area, line}`,
 * or null with nothing to draw (no minute, or no message at all). */
export function sparkPaths(points = [], { width = 96, height = 24 } = {}) {
  const values = (points || []).map((value) => Math.max(0, finite(value) ?? 0));
  const peak = Math.max(0, ...values);
  if (values.length < 2 || peak <= 0) return null;
  const step = width / (values.length - 1);
  const xy = values.map((value, index) => [index * step, height - 1 - (value / peak) * (height - 2)]);
  const line = xy.map(([x, y], index) => `${index ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
  return { line, area: `${line} L${width} ${height} L0 ${height} Z` };
}

/** "Busiest at 23:00, 26 messages", for the sparkline's accessible name. */
export function peakLabel(peak) {
  if (!peak || !(peak.messages > 0)) return "No audience chat";
  const minute = Math.max(0, Math.round(peak.minute || 0));
  const clock = `${minute}:00`;
  return `Audience chat per minute, busiest at ${clock}, ${peak.messages} message${peak.messages === 1 ? "" : "s"}`;
}

/** The row's cells: `{sparkline, sparkLabel, wrote, wroteEstimated, questions,
 * open, lead, analysed}`; "—" for a number the Report hides. `row` null
 * (not loaded yet) gives null. */
export function libraryCells(row, sessionId = "") {
  if (!row || typeof row !== "object") return null;
  const wrote = finite(row.wrote?.value);
  const questions = finite(row.questions?.value);
  const lead = row.lead ? foundFinding(row.lead, "en", String(sessionId || row.sessionId || "")) : null;
  return {
    sparkline: Array.isArray(row.sparkline) ? row.sparkline : [],
    sparkLabel: peakLabel(row.peak),
    wrote: wrote == null ? EMPTY : `${Math.round(wrote * 100)}%`,
    wroteEstimated: wrote != null && Boolean(row.wrote?.estimated),
    questions: questions == null ? EMPTY : String(Math.round(questions)),
    open: row.stillOpen > 0 ? `${row.stillOpen} open` : "",
    lead: lead?.headline || "",
    analysed: Boolean(row.analysed),
  };
}
