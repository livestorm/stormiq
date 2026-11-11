// Steps for the shared loader (`components/ui/ProgressSteps.vue`), from the
// stage a job reports (`livestorm_app/progress.py`). Pure functions.

/** Steps from an ordered list `[{stage, label}]` and the job's current
 * stage: the steps before it done, it current, the rest to do. An unknown
 * or empty stage puts the first step current ("waiting to start"). `detail`
 * goes on the current step. */
export function stepsAt(order, stage, detail = "") {
  const at = Math.max(0, order.findIndex((step) => step.stage === stage));
  return order.map((step, index) => ({
    label: step.label,
    state: index < at ? "done" : index === at ? "current" : "todo",
    detail: index === at ? detail : "",
  }));
}

// A transcript, as the worker reports it (TRANSCRIPT_STAGE_FLOORS).
export const TRANSCRIPT_STEPS = [
  { stage: "queued", label: "Waiting for a free worker" },
  { stage: "fetching_recording", label: "Downloading the recording" },
  { stage: "uploading_to_gladia", label: "Sending it to Gladia" },
  { stage: "transcribing", label: "Transcribing" },
  { stage: "post_processing", label: "Telling the speakers apart" },
  { stage: "persisting", label: "Saving the transcript" },
];

// An AI output, as the worker reports it (ANALYSIS_STAGE_FLOORS).
export const AI_STEPS = [
  { stage: "queued", label: "Waiting for a free worker" },
  { stage: "loading_sources", label: "Reading the session" },
  { stage: "building_prompt", label: "Preparing what the AI reads" },
  { stage: "analyzing", label: "Writing with AI" },
  { stage: "persisting", label: "Checking and saving" },
];

/** An AI output's steps from its job's stage ("" before the worker says:
 * waiting for a free worker). */
export function aiSteps(stage) {
  return stepsAt(AI_STEPS, stage || "queued", stage === "analyzing" ? "The longest step: a minute or two." : "");
}

/** The transcript job's steps: `progress` is the job's `{stage, message}`
 * (the message, Gladia's own, goes on the current step). */
export function transcriptSteps(progress) {
  return stepsAt(TRANSCRIPT_STEPS, progress?.stage || "queued", progress?.message || "");
}
