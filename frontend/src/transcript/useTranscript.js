// The Transcript tab's data: one session's room view, its transcript job and
// its speaker names.
//
// `createTranscriptView()` returns a small reactive model the view owns (one
// per view, not a global store):
//   open(sessionId)     loads `GET /record/transcript`; without speech, asks
//                       once how the transcript job stands, and follows it
//                       while it runs (a session just added is transcribing)
//   makeTranscript()    `POST /fetch-transcript` (a paid Gladia job), only
//                       when the user clicks; `force` tries a failed one again
//   saveNames(names)    `POST /speaker-labels`, then reloads the view
//   close()             cancels everything (the view calls it on unmount)
//
// Scope rules, as in the workspace store (frontend/CLAUDE.md): every request
// carries the open session's AbortController signal; opening another session
// aborts the old one, stops its poll and drops late results. The poll
// survives blips: a network error, 408, 429 or 5xx is retried with backoff,
// and `MAX_FAILURES` in a row stop it; any other error stops it at once.

import { markRaw, reactive } from "vue";
import { api as defaultApi } from "../api";

export const POLL_MS = 5000;
export const MAX_POLL_MS = 60000;
export const MAX_FAILURES = 5;

const IN_FLIGHT = new Set(["pending", "running"]);

function retryable(error) {
  const status = error?.status;
  return !status || status === 408 || status === 429 || status >= 500;
}

function isAbort(error) {
  return error?.name === "AbortError";
}

function hasSpeech(body) {
  return Boolean(body?.speech?.length);
}

export function createTranscriptView({ api = defaultApi } = {}) {
  const state = reactive({
    sessionId: "",
    loading: false,
    error: "",
    body: null, // the route's whole response
    // The transcript job, read only when there is no speech:
    // idle | checking | none | pending | running | error | starting
    job: { status: "idle", error: "" },
    saving: false,
    saveError: "",
  });
  let scope = null;

  function isCurrent(own) {
    return own !== null && own === scope && !own.controller.signal.aborted;
  }

  function stopPoll(own) {
    if (own?.timer) clearTimeout(own.timer);
    if (own) own.timer = null;
  }

  function close() {
    if (scope) {
      stopPoll(scope);
      scope.controller.abort();
    }
    scope = null;
  }

  async function load(own, { quiet = false } = {}) {
    if (!quiet) state.loading = true;
    try {
      const body = await api.getSessionTranscriptView(own.sessionId, { signal: own.controller.signal });
      if (!isCurrent(own)) return;
      // Raw: Vue tracks the property, not the thousands of lines inside it
      // (as the store does with the workspace). Replaced, never mutated.
      state.body = body && typeof body === "object" ? markRaw(body) : body;
      state.error = "";
      if (hasSpeech(body)) state.job = { status: "idle", error: "" };
      else if (!IN_FLIGHT.has(state.job.status)) await checkJob(own);
    } catch (error) {
      if (!isCurrent(own) || isAbort(error)) return;
      if (!quiet) state.error = error?.message || "The transcript could not be loaded.";
    } finally {
      if (isCurrent(own) && !quiet) state.loading = false;
    }
  }

  function schedulePoll(own) {
    stopPoll(own);
    const delay = Math.min(POLL_MS * 2 ** own.failures, MAX_POLL_MS);
    own.timer = setTimeout(() => pollOnce(own), delay);
  }

  // True when the transcript is ready: the route then sends the whole
  // workspace, with no `jobStatus`.
  function applyJob(own, job) {
    const status = job?.jobStatus;
    if (!status) return true;
    if (IN_FLIGHT.has(status)) {
      own.failures = 0;
      const percent = Number(job?.progress?.percent);
      state.job = {
        status,
        error: "",
        progress: Number.isFinite(percent) ? Math.max(0, Math.min(100, Math.round(percent))) : null,
        // The worker's stage and Gladia's own line, for the loader's steps.
        stage: typeof job?.progress?.stage === "string" ? job.progress.stage : "",
        message: typeof job?.progress?.message === "string" ? job.progress.message : "",
      };
      schedulePoll(own);
      return false;
    }
    if (status === "error") {
      state.job = { status: "error", error: job?.error || "The transcript could not be made." };
      return false;
    }
    if (status === "completed") return true;
    state.job = { status: "none", error: "" }; // not_found: never started (or expired)
    return false;
  }

  async function checkJob(own) {
    state.job = { status: "checking", error: "" };
    try {
      const job = await api.getTranscriptJobStatus(own.sessionId, { signal: own.controller.signal });
      if (!isCurrent(own)) return;
      if (applyJob(own, job)) await load(own, { quiet: true });
    } catch (error) {
      if (!isCurrent(own) || isAbort(error)) return;
      state.job = { status: "none", error: "" };
    }
  }

  async function pollOnce(own) {
    if (!isCurrent(own)) return;
    let job;
    try {
      job = await api.getTranscriptJobStatus(own.sessionId, { signal: own.controller.signal });
    } catch (error) {
      if (!isCurrent(own) || isAbort(error)) return;
      if (retryable(error) && own.failures + 1 < MAX_FAILURES) {
        own.failures += 1;
        schedulePoll(own);
        return;
      }
      state.job = { status: "error", error: error?.message || "The transcript's progress could not be read." };
      return;
    }
    if (!isCurrent(own)) return;
    if (applyJob(own, job) || job?.jobStatus === "not_found") {
      // Finished (or its status expired): the view says what was saved.
      state.job = { status: "idle", error: "" };
      await load(own, { quiet: true });
    }
  }

  function open(sessionId) {
    const id = String(sessionId || "").trim();
    if (scope && scope.sessionId === id) return;
    close();
    const own = { sessionId: id, controller: new AbortController(), timer: null, failures: 0 };
    scope = own;
    Object.assign(state, {
      sessionId: id,
      loading: false,
      error: "",
      body: null,
      job: { status: "idle", error: "" },
      saving: false,
      saveError: "",
    });
    if (id) load(own);
  }

  async function makeTranscript({ force = false } = {}) {
    const own = scope;
    if (!own || IN_FLIGHT.has(state.job.status) || state.job.status === "starting") return;
    state.job = { status: "starting", error: "" };
    try {
      const response = await api.fetchSessionTranscript(
        own.sessionId,
        { apiKey: "", forceRefresh: force },
        { signal: own.controller.signal },
      );
      if (!isCurrent(own)) return;
      if (response?.jobStatus) {
        if (applyJob(own, response)) await load(own, { quiet: true });
      } else {
        state.job = { status: "idle", error: "" };
        await load(own, { quiet: true });
      }
    } catch (error) {
      if (!isCurrent(own) || isAbort(error)) return;
      state.job = { status: "error", error: error?.message || "The transcript could not be started." };
    }
  }

  async function saveNames(names) {
    const own = scope;
    if (!own || state.saving) return;
    state.saving = true;
    state.saveError = "";
    try {
      await api.saveSpeakerLabels(own.sessionId, { apiKey: "", speakerNames: names }, { signal: own.controller.signal });
      if (!isCurrent(own)) return;
      await load(own, { quiet: true });
    } catch (error) {
      if (!isCurrent(own) || isAbort(error)) return;
      state.saveError = error?.message || "The names could not be saved.";
    } finally {
      if (isCurrent(own)) state.saving = false;
    }
  }

  return { state, open, close, makeTranscript, saveNames, reload: () => scope && load(scope) };
}
