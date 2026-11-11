// The v1 Report's data: one session's record and its analysis run.
//
// `createSessionReport()` returns a small reactive model the Report view
// owns (one per view, not a global store):
//   open(sessionId)   loads `GET /record`, then asks whether the transcript is
//                     being made (it follows it, then reloads the record and
//                     looks a few times for the analysis "Add" chains after
//                     it) and whether an analysis run is in flight
//   startAnalysis()   `POST /session-analysis`, then polls until it finishes,
//                     and reloads the record so the labels show at once
//   loadChat()        `GET /record/chat` once, when someone first opens a count
//                     that lists message ids: `state.chat.byId` maps a `C` id to
//                     `{id, t, text, team}`. A failure can be retried; a record
//                     with a new `updatedAt` (a re-fetch) drops the chat read
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
// After the transcript, the worker queues the analysis "Add" asked for: the
// page looks for it this many times, POLL_MS apart.
export const CHAIN_CHECKS = 4;

const IN_FLIGHT = new Set(["pending", "running"]);

function retryable(error) {
  const status = error?.status;
  return !status || status === 408 || status === 429 || status >= 500;
}

function isAbort(error) {
  return error?.name === "AbortError";
}

function emptyChat() {
  return { status: "idle", error: "", updatedAt: null, byId: {} }; // idle | loading | ready | error
}

export function createSessionReport({ api = defaultApi } = {}) {
  const state = reactive({
    sessionId: "",
    loading: false,
    error: "",
    record: null, // the route's whole response: {sessionId, updatedAt, meta, record}
    // status: idle | starting | pending | running | completed | error; progress:
    // the run's latest stage from the server (`{stage, percent, started_at_ms}`), or null.
    analysis: { status: "idle", error: "", progress: null },
    // The transcript job, while one runs: idle | running | done | error;
    // `progress` is its latest step from the server (`{message, …}`).
    transcript: { status: "idle", progress: null, error: "" },
    // Opened by "Add": the transcript and the first analysis are being made.
    preparing: false,
    chat: emptyChat(), // the chat's text by id, read on demand
    // "Fetch again from Livestorm": the session, chat and questions read again
    // (free, no transcript), then the record. idle | running | done | error.
    refetch: { status: "idle", error: "" },
  });
  let scope = null;

  function isCurrent(own) {
    return own !== null && own === scope && !own.controller.signal.aborted;
  }

  function stopPoll(own) {
    if (own?.timer) clearTimeout(own.timer);
    if (own?.transcriptTimer) clearTimeout(own.transcriptTimer);
    if (own) {
      own.timer = null;
      own.transcriptTimer = null;
    }
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
      if (state.chat.status !== "idle" && state.chat.updatedAt !== (body?.updatedAt ?? null)) {
        own.chatToken = null; // a chat read in flight belongs to the old payload
        state.chat = emptyChat();
      }
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
      state.preparing = false;
      return;
    }
    if (!isCurrent(own)) return;
    const status = job?.jobStatus;
    if (IN_FLIGHT.has(status)) {
      own.failures = 0;
      state.analysis = { status, error: "", progress: job?.progress ?? null };
      schedulePoll(own);
    } else if (status === "unknown") {
      if (own.failures + 1 < MAX_FAILURES) {
        own.failures += 1;
        schedulePoll(own);
      } else {
        state.analysis = { status: "error", error: "The analysis status could not be read." };
        state.preparing = false;
      }
    } else if (status === "error") {
      state.analysis = { status: "error", error: job?.error || "The analysis failed." };
      state.preparing = false;
    } else {
      // completed, or not_found once its result has expired: the record says what was saved.
      state.analysis = { status: "completed", error: "" };
      await loadRecord(own, { quiet: true });
      if (isCurrent(own)) state.preparing = false;
    }
  }

  async function checkRunInFlight(own) {
    try {
      const job = await api.getSessionAnalysisJob(own.sessionId, { signal: own.controller.signal });
      if (!isCurrent(own)) return;
      if (IN_FLIGHT.has(job?.jobStatus)) {
        state.analysis = { status: job.jobStatus, error: "", progress: job?.progress ?? null };
        schedulePoll(own);
      } else if (job?.jobStatus === "error") {
        state.analysis = { status: "error", error: job?.error || "The last analysis failed." };
      }
    } catch {
      // Not knowing about a run in flight is fine: the button still works.
    }
  }

  // The analysis the worker queues after the transcript: looked for a few
  // times, since it lands a moment after the transcript is saved.
  async function findChainedRun(own) {
    for (let left = CHAIN_CHECKS; left > 0; left -= 1) {
      if (!isCurrent(own)) return;
      await checkRunInFlight(own);
      if (!isCurrent(own) || IN_FLIGHT.has(state.analysis.status) || state.analysis.status === "error") return;
      if (left > 1) await new Promise((resolve) => (own.transcriptTimer = setTimeout(resolve, POLL_MS)));
    }
  }

  // `waits`: with `prepare` ("Add" opened the page), how many more times a
  // transcript job that isn't there yet is looked for before going on.
  async function followTranscript(own, waits = 0) {
    if (!isCurrent(own)) return;
    let job;
    try {
      job = await api.getTranscriptJobStatus(own.sessionId, { signal: own.controller.signal });
    } catch (error) {
      if (!isCurrent(own) || isAbort(error)) return;
      // A blip: look again; the page still shows what it has.
      own.transcriptTimer = setTimeout(() => followTranscript(own), MAX_POLL_MS);
      return;
    }
    if (!isCurrent(own)) return;
    const status = job?.jobStatus;
    if (IN_FLIGHT.has(status)) {
      state.transcript = { status: "running", progress: job?.progress ?? null, error: "" };
      own.transcriptTimer = setTimeout(() => followTranscript(own), POLL_MS);
      return;
    }
    if (state.transcript.status !== "running") {
      // Nothing being made. Just added: the job may not be queued yet, or the
      // transcript was already there and the analysis comes next.
      if (waits > 0) {
        own.transcriptTimer = setTimeout(() => followTranscript(own, waits - 1), POLL_MS);
      } else if (own.prepare) {
        await findChainedRun(own);
        if (isCurrent(own) && !IN_FLIGHT.has(state.analysis.status)) state.preparing = false;
      }
      return;
    }
    state.transcript = status === "error"
      ? { status: "error", progress: null, error: job?.error || "The transcript could not be made." }
      : { status: "done", progress: null, error: "" };
    await loadRecord(own, { quiet: true });
    await findChainedRun(own);
    if (isCurrent(own) && !IN_FLIGHT.has(state.analysis.status)) state.preparing = false;
  }

  async function open(sessionId, { prepare = false } = {}) {
    const id = String(sessionId ?? "").trim();
    close();
    const own = { sessionId: id, controller: new AbortController(), timer: null, transcriptTimer: null, failures: 0, chatToken: null, prepare };
    scope = own;
    state.sessionId = id;
    state.record = null;
    state.error = "";
    state.analysis = { status: "idle", error: "", progress: null };
    state.transcript = { status: "idle", progress: null, error: "" };
    // Just added ("Add" opened the page with `prepare`): the page shows the
    // preparation until the transcript and the first analysis are done.
    state.preparing = prepare;
    state.chat = emptyChat();
    state.refetch = { status: "idle", error: "" };
    if (!id) return;
    await loadRecord(own);
    if (!isCurrent(own) || !state.record) return;
    await checkRunInFlight(own);
    await followTranscript(own, prepare ? CHAIN_CHECKS : 0);
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

  async function loadChat() {
    const own = scope;
    if (!own || !state.record || state.chat.status === "loading" || state.chat.status === "ready") return;
    const token = {};
    own.chatToken = token;
    const updatedAt = state.record.updatedAt ?? null;
    state.chat = { ...emptyChat(), status: "loading", updatedAt };
    try {
      const body = await api.getSessionChat(own.sessionId, { signal: own.controller.signal });
      if (!isCurrent(own) || own.chatToken !== token) return;
      const byId = {};
      for (const message of body?.messages || []) byId[message.id] = message;
      state.chat = { status: "ready", error: "", updatedAt, byId };
    } catch (error) {
      if (!isCurrent(own) || own.chatToken !== token || isAbort(error)) return;
      state.chat = { ...emptyChat(), status: "error", error: error?.message || "The chat could not be loaded.", updatedAt };
    }
  }

  /** Reads the session, its chat and its questions from Livestorm again
   * (`POST /fetch-base` with `forceRefresh`: no transcript, nothing paid),
   * then the record. A chat cached before its messages carried their
   * authors' people links names nobody until this runs. */
  async function refetch() {
    const own = scope;
    if (!own || state.refetch.status === "running") return false;
    state.refetch = { status: "running", error: "" };
    try {
      await api.fetchSessionBase(own.sessionId, { apiKey: "", forceRefresh: true }, { signal: own.controller.signal });
      if (!isCurrent(own)) return false;
      await loadRecord(own, { quiet: true });
      if (!isCurrent(own)) return false;
      state.refetch = { status: "done", error: "" };
      return true;
    } catch (error) {
      if (!isCurrent(own) || isAbort(error)) return false;
      state.refetch = { status: "error", error: error?.message || "The session could not be fetched again." };
      return false;
    }
  }

  return { state, open, close, startAnalysis, loadChat, refetch };
}
