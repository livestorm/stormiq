// The AI outputs' Markdown, as blocks the page renders as text (never as
// HTML): headings, paragraphs and lists, with bold, italic and code inside.
// Model text can hold anything, so nothing here becomes markup: a component
// turns each part into a text node.

const HEADING = /^(#{1,6})\s+(.+?)\s*#*\s*$/;
const ITEM = /^\s*(?:([-*•+])|(\d+)[.)])\s+(.*)$/;
const RULE = /^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/;

/** Inline Markdown as `[{text, bold, italic, code}]`: `**bold**`,
 * `__bold__`, `*italic*`, `_italic_` and `` `code` ``; anything else is
 * text as written. */
export function inlineParts(value) {
  const source = String(value ?? "");
  const pattern = /(\*\*|__)(.+?)\1|(\*|_)(?!\s)(.+?)(?<!\s)\3|`([^`]+)`/g;
  const parts = [];
  let cursor = 0;
  for (const match of source.matchAll(pattern)) {
    if (match.index > cursor) parts.push({ text: source.slice(cursor, match.index) });
    if (match[2] !== undefined) parts.push({ text: match[2], bold: true });
    else if (match[4] !== undefined) parts.push({ text: match[4], italic: true });
    else parts.push({ text: match[5], code: true });
    cursor = match.index + match[0].length;
  }
  if (cursor < source.length) parts.push({ text: source.slice(cursor) });
  return parts.length ? parts : [{ text: "" }];
}

/** Markdown text as `[{type, ...}]` blocks:
 * - `{type: "heading", level, parts}` (levels 1–6 as written);
 * - `{type: "paragraph", parts}`: lines of one paragraph keep their line
 *   breaks (an email's lines);
 * - `{type: "list", ordered, items: [parts]}`.
 * A horizontal rule is dropped. */
export function markdownBlocks(value) {
  const lines = String(value ?? "").replace(/\r\n?/g, "\n").split("\n");
  const blocks = [];
  let paragraph = [];
  let list = null;

  const endParagraph = () => {
    if (paragraph.length) blocks.push({ type: "paragraph", parts: inlineParts(paragraph.join("\n")) });
    paragraph = [];
  };
  const endList = () => {
    if (list) blocks.push(list);
    list = null;
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    if (!line.trim()) {
      endParagraph();
      endList();
      continue;
    }
    if (RULE.test(line)) {
      endParagraph();
      endList();
      continue;
    }
    const heading = line.match(HEADING);
    if (heading) {
      endParagraph();
      endList();
      blocks.push({ type: "heading", level: heading[1].length, parts: inlineParts(heading[2]) });
      continue;
    }
    const item = line.match(ITEM);
    if (item) {
      endParagraph();
      const ordered = item[2] !== undefined;
      if (list && list.ordered !== ordered) endList();
      if (!list) list = { type: "list", ordered, items: [] };
      list.items.push(inlineParts(item[3]));
      continue;
    }
    if (list && /^\s{2,}\S/.test(raw)) {
      // A wrapped list item: its next line, indented.
      const last = list.items[list.items.length - 1];
      list.items[list.items.length - 1] = [...last, { text: " " }, ...inlineParts(line.trim())];
      continue;
    }
    endList();
    paragraph.push(line.trim());
  }
  endParagraph();
  endList();
  return blocks;
}
