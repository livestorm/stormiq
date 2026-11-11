// The chapters' words: pure functions over `GET /record/chapters`
// (docs/api.md, "Chapters"). The server decides the chapters, their times
// and the show notes' text; the page only splits that text into rows and
// says why there is none.

/** The show notes as rows, `[{time, title}]`, from the server's text
 * ("04:12 Title" per line). */
export function noteRows(body) {
  return String(body?.showNotes?.text || "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const space = line.indexOf(" ");
      return space < 0 ? { time: line, title: "" } : { time: line.slice(0, space), title: line.slice(space + 1) };
    });
}

/** Why there are no chapters, from their state, or "" when there are. */
export function emptyReason(body) {
  if (noteRows(body).length) return "";
  if (body?.state === "stale") return "The session changed since it was analysed. Analyse it again on the Report to update its chapters.";
  if (body?.state === "ready") return "The analysis found no chapters in this talk.";
  return "Chapters show here once the session is analysed (Analyse session, on the Report). It needs a transcript.";
}

/** A note when YouTube wouldn't read these as chapters, or "". */
export function youtubeNote(body) {
  if (!noteRows(body).length || body?.showNotes?.youtube) return "";
  return "YouTube reads chapters only when there are at least three, each at least 10 seconds long. Adjust them before you paste them there.";
}
