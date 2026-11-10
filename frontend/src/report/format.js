// Words and dates for the v1 Report header. Pure functions over what the
// record sends: they format, they never compute a measure.

/** "Tuesday 10 November 2026, 14:00", in the session's own time zone when it
 * is a valid IANA zone (`meta.clock.timezone`), in UTC otherwise; "" without
 * a start time. Returns `{ text, zone }`. */
export function formatSessionStart(startedAtUnix, timezone) {
  if (typeof startedAtUnix !== "number" || !Number.isFinite(startedAtUnix)) return { text: "", zone: "" };
  const date = new Date(startedAtUnix * 1000);
  const options = { weekday: "long", day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit" };
  for (const zone of [timezone, "UTC"]) {
    if (!zone) continue;
    try {
      const text = new Intl.DateTimeFormat("en-GB", { ...options, timeZone: zone }).format(date);
      return { text, zone };
    } catch {
      // An unknown zone: fall back to UTC.
    }
  }
  return { text: "", zone: "" };
}

/** "45 min" or "1 h 30 min" from the record's `meta.clock.endS`; "" when unknown. */
export function formatLength(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds <= 0) return "";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

/** What the analysis panel says, from the record's `chatLabels` and the run's
 * state. Returns `{ title, detail, action, busy, tone }`: `action` is the
 * button's label, or "" when there is nothing to run. */
export function analysisSummary(chatLabels, analysis) {
  const status = analysis?.status || "idle";
  if (status === "starting" || status === "pending" || status === "running") {
    return {
      title: "Analysing the session",
      detail: status === "running" ? "Labelling the chat. This takes about a minute." : "Waiting for a free worker.",
      action: "Analysing…",
      busy: true,
      tone: "neutral",
    };
  }
  if (status === "error") {
    return {
      title: "The analysis failed",
      detail: analysis.error || "Nothing was saved. Please try again.",
      action: "Try again",
      busy: false,
      tone: "low",
    };
  }
  const state = chatLabels?.state || "none";
  if (state === "ready") {
    const count = typeof chatLabels.messages === "number" ? chatLabels.messages : null;
    return {
      title: "Analysed",
      detail: count === null ? "The chat is labelled." : `${count} chat ${count === 1 ? "message" : "messages"} labelled.`,
      action: "",
      busy: false,
      tone: "good",
    };
  }
  if (state === "stale") {
    return {
      title: "The chat changed since the last analysis",
      detail: "Run it again to label the new messages.",
      action: "Analyse again",
      busy: false,
      tone: "fair",
    };
  }
  return {
    title: "Not analysed yet",
    detail: "The analysis reads the chat with AI and sorts every message: questions, reactions, tech issues. It runs only when you start it.",
    action: "Analyse session",
    busy: false,
    tone: "neutral",
  };
}
