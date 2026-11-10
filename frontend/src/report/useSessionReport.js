// The v1 Report's data: one session's record and its analysis run.
//
// `createSessionReport()` returns a small reactive model the Report view
// owns (one per view, not a global store):
//   open(sessionId)   loads `GET /record`, then asks once whether an analysis
//                     run is in flight (a teammate may have started one)
//   startAnalysis()   `POST /session-analysis`, then polls until it finishes,
//                     and reloads the record so the labels show at once
//   close()           cancels everything (the view calls it on unmount)
//
// Scope rules, as in the workspace store (frontend/CLAUDE.md): every request
// carries the open session's AbortController signal; opening another session
// aborts the old one, stops its poll and drops late results. A poll survives
// blips: a network error, 408, 429, 5xx or an `unknown` status is retried
// with backoff, and `MAX_FAILURES` in a row stop it; any other error stops
// it at once. The page computes nothing: it shows what the record says.

import { reactive } from "vue";
import { api as defaultApi } from "../api";

export const POLL_MS = 2000;
export const MAX_POLL_MS = 30000;
export const MAX_FAILURES = 5;

const IN_FLIGHT = new Set(["pending", "running"]);

function retryable(error) {
  const status = error?.status;
  return !status || status === 408 || status === 429 || status >= 500;
}

function isAbort(error) {
  return error?.name === "AbortError";
}

export function createSessionReport({ api = defaultApi } = {}) {
  const state = reactive({
    sessionId: "",
    loading: false,
    error: "",
    record: null, // the route's whole response: {sessionId, updatedAt, meta, record}
    analysis: { status: "idle", error: "" }, // idle | starting | pending | running | completed | error
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

  async function loadRecord(own, { quiet = false } = {}) {
    if (!quiet) state.loading = true;
    try {
      const body = await api.getSessionRecord(own.sessionId, { signal: own.controller.signal });
      if (!isCurrent(own)) return;
      state.record = body;
      state.error = "";
    } catch (error) {
      if (!isCurrent(own) || isAbort(error)) return;
      if (!quiet) state.error = error?.message || "The session could not be loaded.";
    } finally {
      if (isCurrent(own) && !quiet) state.loading = false;
    }
  }

  function schedulePoll(own) {
    stopPoll(own);
    const delay = Math.min(POLL_MS * 2 ** own.failures, MAX_POLL_MS);
    own.timer = setTimeout(() => pollOnce(own), delay);
  }

  async function pollOnce(own) {
    if (!isCurrent(own)) return;
    let job;
    try {
      job = await api.getSessionAnalysisJob(own.sessionId, { signal: own.controller.signal });
    } catch (error) {
      if (!isCurrent(own) || isAbort(error)) return;
      if (retryable(error) && own.failures + 1 < MAX_FAILURES) {
        own.failures += 1;
        schedulePoll(own);
        return;
      }
      state.analysis = { status: "error", error: error?.message || "The analysis status could not be read." };
      return;
    }
    if (!isCurrent(own)) return;
    const status = job?.jobStatus;
    if (IN_FLIGHT.has(status)) {
      own.failures = 0;
      state.analysis = { status, error: "" };
      schedulePoll(own);
    } else if (status === "unknown") {
      if (own.failures + 1 < MAX_FAILURES) {
        own.failures += 1;
        schedulePoll(own);
      } else {
        state.analysis = { status: "error", error: "The analysis status could not be read." };
      }
    } else if (status === "error") {
      state.analysis = { status: "error", error: job?.error || "The analysis failed." };
    } else {
      // completed, or not_found once its result has expired: the record says what was saved.
      state.analysis = { status: "completed", error: "" };
      await loadRecord(own, { quiet: true });
    }
  }

  async function checkRunInFlight(own) {
    try {
      const job = await api.getSessionAnalysisJob(own.sessionId, { signal: own.controller.signal });
      if (!isCurrent(own)) return;
      if (IN_FLIGHT.has(job?.jobStatus)) {
        state.analysis = { status: job.jobStatus, error: "" };
        schedulePoll(own);
      } else if (job?.jobStatus === "error") {
        state.analysis = { status: "error", error: job?.error || "The last analysis failed." };
      }
    } catch {
      // Not knowing about a run in flight is fine: the button still works.
    }
  }

  async function open(sessionId) {
    const id = String(sessionId ?? "").trim();
    close();
    const own = { sessionId: id, controller: new AbortController(), timer: null, failures: 0 };
    scope = own;
    state.sessionId = id;
    state.record = null;
    state.error = "";
    state.analysis = { status: "idle", error: "" };
    if (!id) return;
    await loadRecord(own);
    if (isCurrent(own) && state.record) await checkRunInFlight(own);
  }

  async function startAnalysis() {
    const own = scope;
    if (!own || IN_FLIGHT.has(state.analysis.status) || state.analysis.status === "starting") return;
    state.analysis = { status: "starting", error: "" };
    try {
      const job = await api.startSessionAnalysis(own.sessionId, { signal: own.controller.signal });
      if (!isCurrent(own)) return;
      own.failures = 0;
      state.analysis = { status: IN_FLIGHT.has(job?.jobStatus) ? job.jobStatus : "pending", error: "" };
      schedulePoll(own);
    } catch (error) {
      if (!isCurrent(own) || isAbort(error)) return;
      state.analysis = { status: "error", error: error?.message || "The analysis could not be started." };
    }
  }

  return { state, open, close, startAnalysis };
}
