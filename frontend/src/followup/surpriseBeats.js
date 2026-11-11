// Surprise me, played: the take cut into beats the player shows one at a
// time and the browser reads aloud (`components/report/SurprisePlayer.vue`).
// Pure functions over the take's text (already checked by the server: its
// numbers are the session's, no attendee is named) and the session's
// chapter titles. No model call.
//
// - A beat is a sentence. The title opens; the last sentence of each
//   paragraph is its punchline (the prompt writes setup → twist → punch).
// - Each beat's words are split into parts: `number` (a figure, "56%",
//   "23:00", "1,200"), `keyword` (a word of a chapter title, or an acronym
//   like SSO; at most MAX_KEYWORDS a beat, so it stays a highlight), `plain`.

export const MAX_KEYWORDS = 2;

const STOPWORDS = new Set(
  `about after again also and are because been before being between both but can could does doing down during each
  even every from further have having here into just like more most much must only other over same should since some
  such than that their them then there these they this those through under until very what when where which while who
  whom will with would your yours ours what's it's that's there's here's session today live demo with`.split(/\s+/),
);

const NUMBER = /^(?:\d{1,2}:\d{2}(?::\d{2})?|\d[\d,]*(?:\.\d+)?%?)$/;
const ACRONYM = /^[A-Z][A-Z0-9]{1,5}s?$/;

function plainText(markdown) {
  return String(markdown ?? "")
    .replace(/\r\n/g, "\n")
    .replace(/\*\*|__|`|\*|_/g, "")
    .trim();
}

/** The words worth a highlight: each chapter title's words of 4 letters or
 * more that aren't common words, lower case. */
export function keywordsFrom(chapterTitles = []) {
  const words = new Set();
  for (const title of chapterTitles) {
    for (const word of String(title ?? "").toLowerCase().match(/[\p{L}][\p{L}'-]*/gu) || []) {
      if (word.length >= 4 && !STOPWORDS.has(word)) words.add(word);
    }
  }
  return words;
}

function sentencesOf(paragraph) {
  const found = paragraph.match(/[^.!?…]+(?:[.!?…]+["”’)]*|$)/g) || [];
  return found.map((sentence) => sentence.trim()).filter(Boolean);
}

function partsOf(sentence, keywords) {
  const parts = [];
  let highlighted = 0;
  for (const token of sentence.split(/(\s+)/)) {
    if (!token) continue;
    const core = token.replace(/^[("“‘'«]+|[)"”’'».,!?;:…]+$/g, "");
    const lead = token.slice(0, token.indexOf(core));
    const tail = token.slice(token.indexOf(core) + core.length);
    let kind = "plain";
    if (core && NUMBER.test(core)) kind = "number";
    else if (core && highlighted < MAX_KEYWORDS && (keywords.has(core.toLowerCase()) || ACRONYM.test(core))) {
      kind = "keyword";
      highlighted += 1;
    }
    if (kind === "plain") {
      const last = parts[parts.length - 1];
      if (last?.kind === "plain") last.text += token;
      else parts.push({ kind, text: token });
    } else {
      if (lead) parts.push({ kind: "plain", text: lead });
      parts.push({ kind, text: core });
      if (tail) parts.push({ kind: "plain", text: tail });
    }
  }
  return parts;
}

/** The take as beats: `[{kind, text, parts}]`, `kind` title | line | punch.
 * `take` is the Surprise me text as Follow-up shows it (`cleanRecap`: a
 * "## Title" line, then paragraphs). Empty for an empty take. */
export function surpriseBeats(take, chapterTitles = []) {
  const keywords = keywordsFrom(chapterTitles);
  const lines = plainText(take).split("\n");
  const beats = [];
  let paragraph = [];
  const flush = () => {
    const sentences = sentencesOf(paragraph.join(" ").replace(/\s+/g, " ").trim());
    sentences.forEach((sentence, index) => {
      beats.push({
        kind: index === sentences.length - 1 && sentences.length > 1 ? "punch" : "line",
        text: sentence,
        parts: partsOf(sentence, keywords),
      });
    });
    paragraph = [];
  };
  for (const raw of lines) {
    const line = raw.trim();
    const heading = /^#{1,6}\s+(.*)$/.exec(line);
    if (heading) {
      flush();
      if (heading[1].trim()) beats.push({ kind: "title", text: heading[1].trim(), parts: partsOf(heading[1].trim(), keywords) });
    } else if (!line) {
      flush();
    } else {
      paragraph.push(line);
    }
  }
  flush();
  return beats;
}

/** A number part's value to count up to, and how to print it on the way:
 * `{value, format}`, or null for one that doesn't count (a clock time). */
export function countUp(text) {
  const match = /^(\d[\d,]*(?:\.\d+)?)(%?)$/.exec(String(text ?? ""));
  if (!match) return null;
  const value = Number(match[1].replace(/,/g, ""));
  if (!Number.isFinite(value) || value <= 1) return null;
  const decimals = (match[1].split(".")[1] || "").length;
  const grouped = match[1].includes(",");
  return {
    value,
    format: (n) => {
      const fixed = n.toFixed(decimals);
      const shown = grouped ? Number(fixed).toLocaleString("en-US", { minimumFractionDigits: decimals }) : fixed;
      return `${shown}${match[2]}`;
    },
  };
}
