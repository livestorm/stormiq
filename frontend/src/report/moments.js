// The Pulse's key moments, told, worded from `GET /record/moments`: for each
// moment, when it was, what kind of reaction it was, what was being said,
// what people wrote and the questions asked then. The server finds and
// counts; this module only words. Pure functions.

import { clockLabel } from "./chartGeometry";
import { plural } from "./vitals";

const KINDS = {
  reply: { label: "Answering the stage", icon: "message-circle", line: "The room answered a question from the stage." },
  content: { label: "The room reacted", icon: "zap", line: "The room reacted on its own to what was being said." },
  social: { label: "Greetings and thanks", icon: "users", line: "Mostly greetings, thanks or logistics, not a reaction to the content." },
  tech: { label: "Sound or connection trouble", icon: "info", line: "People reported sound or connection trouble." },
  unknown: { label: "A busy moment", icon: "activity", line: "Analyse the session to tell what kind of reaction this was." },
};

const THEME_WORDS = {
  content: ["reaction to the content", "reactions to the content"],
  question: ["question", "questions"],
  tech: ["sound or connection complaint", "sound or connection complaints"],
  logistics: ["logistics message", "logistics messages"],
  social: ["greeting or thanks", "greetings and thanks"],
  laughter: ["laugh", "laughs"],
  off_topic: ["off-topic message", "off-topic messages"],
};

function span(start, end) {
  return `${clockLabel(start)}–${clockLabel(end)}`;
}

function themesLine(themes = {}) {
  const parts = Object.entries(themes)
    .filter(([key, n]) => n > 0 && THEME_WORDS[key])
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3)
    .map(([key, n]) => plural(n, ...THEME_WORDS[key]));
  return parts.length ? `In the chat: ${parts.join(", ")}.` : "";
}

function line(item) {
  return { id: item.id, time: clockLabel(item.t), author: item.author || "An attendee", text: item.text };
}

/** One moment: `{n, when, kind, icon, takeaway, facts, prompt, said,
 * written, asked, themes}`. `said` is `{label, text}`, or null without
 * speech or when the moment answers a prompt (the prompt is shown). */
export function momentView(moment) {
  const kind = KINDS[moment.kind] || KINDS.unknown;
  const facts = [
    plural(moment.messages, "message", "messages") + (moment.people ? ` from ${plural(moment.people, "person", "people")}` : ""),
  ];
  if (moment.questions) facts.push(plural(moment.questions, "question", "questions"));
  facts.push(`up to ${moment.peak} a minute`);
  // A reply shows the stage's prompt; what was said then is that prompt.
  const said =
    moment.said && !moment.prompt
      ? {
          label: `${moment.said.precise ? "At" : "Around"} ${clockLabel(moment.said.t)}, ${moment.said.speakers.join(" and ")} was saying:`,
          text: moment.said.text,
        }
      : null;
  return {
    n: moment.n,
    when: span(moment.start, moment.end),
    kind: kind.label,
    icon: kind.icon,
    takeaway: kind.line,
    facts: facts.join(" · "),
    prompt: moment.prompt ? { time: clockLabel(moment.prompt.t), text: moment.prompt.quote } : null,
    said,
    written: (moment.written || []).map(line),
    asked: (moment.asked || []).map(line),
    themes: themesLine(moment.themes),
  };
}

/** `{intro, moments, empty}`, or null before it loads. */
export function momentsView(body) {
  if (!body) return null;
  if (body.state === "few_messages") return { intro: "", moments: [], empty: "Too few messages in the live chat to pick out moments." };
  if (body.state !== "ready") return { intro: "", moments: [], empty: "No session clock, so the moments can't be placed." };
  if (!body.moments?.length) return { intro: "", moments: [], empty: "No minute stood out in the chat." };
  const usual = body.usualPerMinute;
  const pace = typeof usual === "number" ? ` The chat usually ran at ${usual} ${usual === 1 ? "message" : "messages"} a minute.` : "";
  const intro = body.surges
    ? `The ${plural(body.moments.length, "moment", "moments")} the chat jumped well above its usual pace, numbered as on the chart.${pace}`
    : `No minute stood far above the rest; these were the busiest, numbered as on the chart.${pace}`;
  const speech = body.speech ? "" : " What was said isn't shown: the recording couldn't be placed on the session clock.";
  return { intro: intro + speech, moments: body.moments.map(momentView), empty: "" };
}

/** The Pulse's numbered pins, `[{n, start, end, title}]` in seconds. */
export function momentPins(body) {
  return (body?.moments || []).map((moment) => ({
    n: moment.n,
    start: moment.start,
    end: moment.end,
    title: `${moment.n}. ${span(moment.start, moment.end)}: ${(KINDS[moment.kind] || KINDS.unknown).label}, ${plural(moment.messages, "message", "messages")}`,
  }));
}
