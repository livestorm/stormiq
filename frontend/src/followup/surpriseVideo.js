// Surprise me's video output: what the page shows for the show's state
// (`GET /surprise-show`, read by `useSurpriseShow.js`). Pure functions.
//
// `videoView(body, { starting, error })` →
//   { mode, failure, steps, percent, version, durationS, sizeBytes, fellBack, voiceLabel, hasTake }
// mode: "making" (the show job or the render is under way, or the click is
// being sent), "ready" (the MP4 is made from the show as it is), "failed"
// (the job or the render failed: its message, Try again), or "idle" (no
// video yet: the button makes it).

import { showSteps } from "../report/progress";

const IN_FLIGHT = new Set(["pending", "running"]);

export function videoView(body, { starting = false, error = "" } = {}) {
  const state = body?.state || "";
  const video = body?.video || {};
  const making = Boolean(starting) || IN_FLIGHT.has(state);
  const failure = error || (state === "error" ? body?.error || video.error || "The video could not be made." : "");
  let mode = "idle";
  if (making) mode = "making";
  else if (state === "ready" && video.state === "ready") mode = "ready";
  else if (failure) mode = "failed";
  const percent = typeof body?.progress?.percent === "number" ? body.progress.percent : null;
  return {
    mode,
    failure: mode === "making" ? "" : failure,
    steps: showSteps(body?.progress),
    percent,
    version: String(video.version || ""),
    durationS: typeof video.durationS === "number" ? video.durationS : null,
    sizeBytes: typeof video.sizeBytes === "number" ? video.sizeBytes : null,
    fellBack: Boolean(body?.voice?.fellBack),
    voiceLabel: String(body?.voice?.label || ""),
    hasTake: Array.isArray(body?.beats) && body.beats.length > 0,
  };
}

/** "1:58" for 117.6 s; "" when unknown. */
export function durationLabel(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds <= 0) return "";
  const total = Math.round(seconds);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/** "7.6 MB" for 8,011,000 bytes; "" when unknown. */
export function sizeLabel(bytes) {
  if (typeof bytes !== "number" || !Number.isFinite(bytes) || bytes <= 0) return "";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** The button's words: the first show, a show whose video isn't made yet, or a retry. */
export function makeLabel(view) {
  if (view.mode === "failed") return "Try again";
  return view.hasTake ? "Make the video" : "Make the show";
}
