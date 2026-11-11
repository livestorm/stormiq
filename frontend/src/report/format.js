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

/** "1:05" or "1:02:05", the time since `startedAtMs` at `nowMs`; "" when
 * either is unknown. */
export function elapsedLabel(startedAtMs, nowMs) {
  if (typeof startedAtMs !== "number" || typeof nowMs !== "number") return "";
  const total = Math.max(0, Math.floor((nowMs - startedAtMs) / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
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

// What each stage of a run says (`progress.SESSION_ANALYSIS_STAGE_FLOORS`).
const STAGE_DETAIL = {
  labelling: "Labelling the chat. This takes about a minute.",
  reading: "Reading the transcript: the chapters, the answers given in the talk and the figures said. A few minutes on a long session.",
  saving: "Checking what was read against the transcript, and saving it.",
  done: "Finishing.",
};
// Which step each stage belongs to: 0 the chat, 1 the transcript.
const STAGE_STEP = { labelling: 0, reading: 1, saving: 1, done: 2 };

/** The run's steps for the panel: `[{label, state}]`, `state` `done`,
 * `current` or `todo`, from the stage the server sent. Without a
 * transcript there is one step. */
export function analysisSteps(stage, { transcript = true } = {}) {
  const at = stage in STAGE_STEP ? STAGE_STEP[stage] : -1;
  const steps = [{ label: "Label the chat" }];
  if (transcript) steps.push({ label: "Read the transcript" });
  return steps.map((step, index) => ({ ...step, state: index < at ? "done" : index === at ? "current" : "todo" }));
}

/** What the analysis panel says, from the record's `chatLabels` and the run's
 * state. Returns `{ title, detail, action, busy, tone }`, and while a run is
 * in flight `steps` (`analysisSteps`), `percent` (the stage's floor, from
 * the server, or null) and `startedAtMs` (when the worker started it, or
 * null): `action` is the button's label, or "" when there is nothing to
 * run. `transcript` says whether the session has one (a step more). */
export function analysisSummary(chatLabels, analysis, { transcript = true, reading = "" } = {}) {
  const status = analysis?.status || "idle";
  if (status === "starting" || status === "pending" || status === "running") {
    // A queued run has no stage of its own yet: what Redis holds may be the last run's.
    const progress = status === "running" ? analysis?.progress : null;
    const stage = progress?.stage in STAGE_STEP ? progress.stage : status === "running" ? "labelling" : "";
    const percent = typeof progress?.percent === "number" && Number.isFinite(progress.percent) ? progress.percent : null;
    const started = progress?.started_at_ms;
    return {
      title: "Analysing the session",
      detail: stage ? STAGE_DETAIL[stage] : "Waiting for a free worker.",
      action: "Analysing…",
      busy: true,
      tone: "neutral",
      steps: analysisSteps(stage, { transcript }),
      percent,
      startedAtMs: typeof started === "number" && Number.isFinite(started) ? started : null,
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
  // The chat is labelled, but the session itself was never read (the
  // analysis ran before the transcript existed), or was read from a session
  // that has changed since: what builds on it (chapters, located answers,
  // Before you share, the Session analysis) needs another run.
  // `reading` is the state of `GET /record/chapters` ("" while unknown).
  if (state === "ready" && transcript && (reading === "none" || reading === "stale")) {
    return {
      title: reading === "stale" ? "The session changed since it was read" : "The chat is labelled, the session isn't read yet",
      detail:
        reading === "stale"
          ? "Run the analysis again to read the session as it is now: the chapters, the answers and the Session analysis build on it."
          : "Run the analysis to read the transcript too: the chapters, the answers and the Session analysis build on it.",
      action: reading === "stale" ? "Analyse again" : "Analyse session",
      busy: false,
      tone: "fair",
    };
  }
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
    detail: "The analysis reads the chat and the transcript with AI: it sorts every message (questions, reactions, tech issues) and notes the chapters, the answers given and the figures said. It runs only when you start it.",
    action: "Analyse session",
    busy: false,
    tone: "neutral",
  };
}

/** The preparation of a session just added ("Add"): what the page shows
 * until its transcript and first analysis are done. `transcript` and
 * `analysis` are the Report's states. Returns `{title, detail, steps}`, each
 * step `{label, state, detail}` (done | current | todo | skipped). */
export function preparationView(transcript, analysis) {
  const t = transcript?.status || "idle";
  const a = analysis?.status || "idle";
  const analysing = a === "pending" || a === "running" || a === "starting";
  const transcriptStep =
    t === "running"
      ? { state: "current", detail: transcript?.progress?.message || "Sending the recording for transcription." }
      : t === "error"
        ? { state: "skipped", detail: "No transcript: the recording couldn't be transcribed, so the chat is analysed on its own." }
        : { state: "done", detail: "" };
  const stage = analysis?.progress?.stage;
  const analysisStep = analysing
    ? { state: "current", detail: stage ? STAGE_DETAIL[stage] || "" : "Waiting for a free worker." }
    : a === "completed"
      ? { state: "done", detail: "" }
      : { state: "todo", detail: "" };
  const steps = [
    { label: "Fetched from Livestorm", state: "done", detail: "The session, its chat, questions and people." },
    { label: "Transcribing the recording", ...transcriptStep },
    { label: "Reading the chat and the talk with AI", ...analysisStep },
    { label: "Your report", state: a === "completed" ? "current" : "todo", detail: "" },
  ];
  return {
    title: "Preparing your report",
    detail:
      t === "running"
        ? "A long recording takes a few minutes to transcribe. It keeps going if you leave this page."
        : "Almost there. It keeps going if you leave this page.",
    steps,
  };
}
