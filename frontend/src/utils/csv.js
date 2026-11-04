// CSV export shared by every table download.
//
// Spreadsheet apps run a cell that starts with = + - @ (or a tab / carriage
// return) as a formula. Chat messages, questions and names are written by
// webinar attendees, so an exported cell like `=HYPERLINK(...)` must stay
// text: such cells get a leading apostrophe. Numbers, and strings that are
// plain numbers ("-5", "+3.2"), are left as they are.

const FORMULA_START = /^[=+\-@\t\r]/;
const PLAIN_NUMBER = /^[+-]?\d+(\.\d+)?$/;

export function escapeCsvCell(value) {
  let text = String(value ?? "");
  if (typeof value === "string" && FORMULA_START.test(text) && !PLAIN_NUMBER.test(text)) {
    text = `'${text}`;
  }
  if (/[",\n\r]/.test(text)) {
    return `"${text.replace(/"/g, '""')}"`;
  }
  return text;
}

export function buildCsv(columns, rows, headerLabel = (column) => column) {
  const header = columns.map((column) => escapeCsvCell(headerLabel(column))).join(",");
  const lines = rows.map((row) => columns.map((column) => escapeCsvCell(row?.[column])).join(","));
  return [header, ...lines].join("\n");
}
