// The "In short" card's points: what the rest of the Report found, said
// first. The strongest findings' headlines (`report/findings.js`), the
// moment the room reacted most (`report/moments.js`'s data) and the
// questions still open (the question desk's counts). Every value comes from
// the server; this module only picks and words. Pure functions.

import { clockLabel } from "./chartGeometry";
import { findingsView } from "./findings";
import { filterCounts } from "../followup/desk";
import { plural } from "./vitals";

const MAX_FINDINGS = 2;
const QUOTE_CHARS = 110;

function clip(text) {
  const words = String(text || "").trim();
  return words.length > QUOTE_CHARS ? `${words.slice(0, QUOTE_CHARS).replace(/\s+\S*$/, "")}…` : words;
}

/** The moment to name: the room's own reaction, else a reply to the stage,
 * with the most messages. Greetings and sound trouble aren't "the" moment. */
function topMoment(moments) {
  const pool = (moments?.moments || []).filter((m) => m.kind === "content" || m.kind === "reply");
  return pool.sort((a, b) => b.messages - a.messages || a.start - b.start)[0] || null;
}

/** `[{icon, text}]`, at most four, in the order a host reads them. */
export function shortPoints({ findings = null, moments = null, desk = null } = {}) {
  const points = [];
  for (const finding of findingsView(findings, "en").found.slice(0, MAX_FINDINGS)) {
    points.push({ icon: "target", text: /[.!?]$/.test(finding.headline) ? finding.headline : `${finding.headline}.` });
  }
  const moment = topMoment(moments);
  if (moment) {
    const when = `${clockLabel(moment.start)}–${clockLabel(moment.end)}`;
    const what = moment.kind === "reply" && moment.prompt
      ? `answering “${clip(moment.prompt.quote)}”`
      : moment.said
        ? `while ${moment.said.speakers.join(" and ")} was saying “${clip(moment.said.text)}”`
        : "";
    points.push({
      icon: "zap",
      text: `The room reacted most at ${when}: ${plural(moment.messages, "message", "messages")}${moment.people ? ` from ${plural(moment.people, "person", "people")}` : ""}${what ? `, ${what}` : ""}.`,
    });
  }
  if (desk?.summary) {
    const counts = filterCounts(desk.summary);
    if (counts.open) points.push({ icon: "help-circle", text: `${plural(counts.open, "question is", "questions are")} still open: answer them on Follow-up.` });
    else if (counts.all) points.push({ icon: "check", text: `All ${plural(counts.all, "question", "questions")} were answered.` });
  }
  return points;
}
