import { computed, markRaw, reactive, watch } from "vue";
import { api } from "../api";

function emptyAiJobs() {
  return {
    transcript: null,
    overall_analysis: null,
    deep_analysis: null,
    smart_recap: null,
    content_repurposing: null,
  };
}

const state = reactive({
  apiKey: "",
  auth: {
    oauthEnabled: false,
    connectedUser: null,
    allowLocalApiKeyFallback: false,
  },
  inputMode: "session",
  sessionId: "",
  eventId: "",
  workspaceEvents: [],
  workspaceEventsNextPage: null,
  workspaceEventsTitle: "",
  workspaceEventsStatus: "",
  selectedWorkspaceEventId: "",
  loadedEventId: "",
  eventSessions: [],
  selectedEventSessionId: "",
  outputLanguage: "English",
  // The session on screen. Stored with markRaw (see setWorkspace): Vue
  // tracks the property itself, not the thousands of rows inside it, so
  // every update replaces the object instead of mutating it.
  workspace: null,
  // List of cached sessions for the current user's Livestorm
  // org. Powers the Single Analysis card grid. Loaded on demand via
  // loadWorkspaceSessions(); refreshed after every successful fetch
  // (new sessions appear in the list).
  workspaceSessions: [],
  workspaceSessionsLoadedAt: 0,
  transcriptUnavailableReason: "",
  transcriptJobProgress: null,
  loading: {
    workspaceEvents: false,
    workspaceSessions: false,
    eventSessions: false,
    sessionFetch: false,
    analysis: false,
    deepAnalysis: false,
    smartRecap: false,
    contentRepurposing: false,
    speakerLabels: false,
  },
  // Live progress for in-flight jobs. Keyed by jobKind so each view
  // can render its own bar without coupling to the others. Each entry:
  //   { jobId, jobStatus, progress: { stage, percent, label } | null,
  //     language?, tone?, error? }
  // Cleared to null when the job lands in the cache. The `transcript` slot
  // mirrors the AI flows so all loading-state UIs use <AiJobProgress>.
  aiJobs: emptyAiJobs(),
  error: "",
});

const activeSessionId = computed(() =>
  state.inputMode === "session" ? state.sessionId.trim() : state.selectedEventSessionId.trim()
);

function workspaceHasTranscript(workspace) {
  return (
    Boolean(workspace?.payloads?.transcript) ||
    (workspace?.tables?.transcriptSegments?.length || 0) > 0 ||
    Boolean(String(workspace?.text?.transcriptDisplay || "").trim())
  );
}

const hasTranscriptData = computed(() => workspaceHasTranscript(state.workspace));

const isTranscriptUnavailable = computed(
  () => Boolean(state.workspace) && !hasTranscriptData.value && Boolean(String(state.transcriptUnavailableReason || "").trim())
);

const isTranscriptLoading = computed(
  () => Boolean(state.workspace) && state.loading.sessionFetch && !hasTranscriptData.value && !isTranscriptUnavailable.value
);

function setWorkspace(workspace) {
  state.workspace = workspace ? markRaw(workspace) : null;
}

function getFriendlyTranscriptUnavailableMessage(message) {
  const normalized = String(message || "").toLowerCase();
  if (normalized.includes("no mp4 video recording found")) {
    return "Transcript isn’t available for this session because Livestorm does not expose a usable MP4 recording for it. Session Overview and Chat & Questions can still work, but Transcript, Analysis, Repurposing, and Smart Recap require a video recording.";
  }
  return String(message || "").trim();
}

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

// ── Session scope ─────────────────────────────────────────────────────────
//
// Every load, job start, poll and save runs inside the scope of the session
// it started for. Opening another session (or signing out) replaces the
// scope: in-flight requests are aborted, pollers stop, and late results are
// dropped instead of being written over the new session's state.

class StaleSessionError extends Error {
  constructor() {
    super("The session changed while this request was in flight.");
    this.name = "StaleSessionError";
  }
}

let scope = { id: "", controller: new AbortController() };

// Loading flags owned by a session's work. A flag is cleared only by the
// call that set it last, so one flow's `finally` can't switch off another's.
const SESSION_LOADING_FLAGS = ["sessionFetch", "analysis", "deepAnalysis", "smartRecap", "contentRepurposing", "speakerLabels"];
const loadingOwners = {};

function openSessionScope(id) {
  scope.controller.abort();
  scope = { id, controller: new AbortController() };
  for (const flag of SESSION_LOADING_FLAGS) {
    delete loadingOwners[flag];
    state.loading[flag] = false;
  }
  return scope;
}

function isCurrent(sessionScope) {
  return sessionScope === scope && !sessionScope.controller.signal.aborted;
}

function assertCurrent(sessionScope) {
  if (!isCurrent(sessionScope)) throw new StaleSessionError();
}

function isStale(error) {
  return error instanceof StaleSessionError || error?.name === "AbortError";
}

// The scope of the session on screen, or null when none is loaded.
function displayedSessionScope() {
  const id = String(state.workspace?.sessionId || "").trim();
  return id && id === scope.id ? scope : null;
}

function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new StaleSessionError());
      return;
    }
    const onAbort = () => {
      clearTimeout(timer);
      reject(new StaleSessionError());
    };
    const timer = setTimeout(() => {
      // Long polls sleep hundreds of times on one signal: don't pile up listeners.
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

function beginLoading(flag) {
  const token = {};
  loadingOwners[flag] = token;
  state.loading[flag] = true;
  return token;
}

function endLoading(flag, token) {
  if (loadingOwners[flag] === token) {
    delete loadingOwners[flag];
    state.loading[flag] = false;
  }
}

// Run `fn` with a loading flag and error reporting. Inside a session scope,
// errors after the user moved on are dropped (resolves to undefined).
async function wrapCall(flag, fn, sessionScope = null) {
  const owns = () => !sessionScope || isCurrent(sessionScope);
  const token = beginLoading(flag);
  if (owns()) state.error = "";
  try {
    return await fn();
  } catch (error) {
    if (sessionScope && isStale(error)) return undefined;
    if (owns()) state.error = errorMessage(error);
    throw error;
  } finally {
    endLoading(flag, token);
  }
}

// ── Polling ───────────────────────────────────────────────────────────────
//
// A brief network or server problem must not end a poll that may be waiting
// on a 90-minute transcription: transient failures (network errors, 408,
// 429, 5xx) are retried with backoff, and only MAX_POLL_FAILURES in a row
// give up. Anything else (401, 403, 404...) stops at once.

const MAX_POLL_FAILURES = 5;
const MAX_POLL_BACKOFF_MS = 60_000;

function isTransientFailure(error) {
  if (isStale(error)) return false;
  const status = error?.status;
  if (status === undefined) return true; // network error: fetch rejected
  return status === 408 || status === 429 || status >= 500;
}

class PollFailures {
  constructor() {
    this.count = 0;
  }

  // Record a failure; rethrows when it isn't worth retrying.
  record(error) {
    if (!isTransientFailure(error)) throw error;
    this.count += 1;
    if (this.count >= MAX_POLL_FAILURES) throw error;
  }

  reset() {
    this.count = 0;
  }

  delay(intervalMs) {
    return Math.min(intervalMs * 2 ** this.count, MAX_POLL_BACKOFF_MS);
  }
}

// A poll answered "unknown": the server couldn't reach its job queue. Counts
// as a transient failure.
function queueUnavailable(what) {
  return Object.assign(new Error(`${what} job status is temporarily unavailable.`), { status: 503 });
}

const TRANSCRIPT_POLL_INTERVAL_MS = 6000;
const TRANSCRIPT_POLL_TIMEOUT_MS = 90 * 60 * 1000; // enough for very long recordings

async function pollTranscriptJob(sessionScope) {
  const { id, controller } = sessionScope;
  const signal = controller.signal;
  const deadline = Date.now() + TRANSCRIPT_POLL_TIMEOUT_MS;
  const failures = new PollFailures();
  const clearProgress = () => {
    if (!isCurrent(sessionScope)) return;
    state.transcriptJobProgress = null;
    state.aiJobs.transcript = null;
  };

  try {
    while (Date.now() < deadline) {
      await sleep(failures.delay(TRANSCRIPT_POLL_INTERVAL_MS), signal);
      let status;
      try {
        status = await api.getTranscriptJobStatus(id, { signal });
      } catch (error) {
        failures.record(error);
        continue;
      }
      if (status?.jobStatus === "unknown") {
        failures.record(queueUnavailable("Transcript"));
        continue;
      }
      failures.reset();
      assertCurrent(sessionScope);
      // When the job finishes the backend returns the full workspace
      // payload (no jobStatus field), so we can use it directly.
      if (!status?.jobStatus) return status;
      // Legacy DB shape (Gladia step + message) for the "Transcript still
      // loading" copy in the AI views; Redis stage-floor shape for the bar.
      if (status.progress) state.transcriptJobProgress = status.progress;
      if (status.progressRedis) {
        state.aiJobs.transcript = { jobId: status.jobId, jobStatus: status.jobStatus, progress: status.progressRedis };
      }
      if (status.jobStatus === "completed") {
        const cached = await api.getCachedSession(id, { signal });
        if (cached) return cached;
      }
      if (status.jobStatus === "error") throw new Error(status.error || "Transcript generation failed.");
      if (status.jobStatus === "not_found") throw new Error("The transcription job was not found. Please try again.");
    }
    throw new Error("Transcript generation timed out. Please try refreshing the page later.");
  } finally {
    clearProgress();
  }
}

const AI_POLL_INTERVAL_MS = 4000;
const AI_POLL_TIMEOUT_MS = 30 * 60 * 1000; // well above any legitimate AI flow runtime

async function pollAiJob(sessionScope, jobKind, pollFn) {
  const signal = sessionScope.controller.signal;
  const deadline = Date.now() + AI_POLL_TIMEOUT_MS;
  const failures = new PollFailures();
  try {
    while (Date.now() < deadline) {
      await sleep(failures.delay(AI_POLL_INTERVAL_MS), signal);
      let response;
      try {
        response = await pollFn(signal);
      } catch (error) {
        failures.record(error);
        continue;
      }
      if (response?.jobStatus === "unknown") {
        failures.record(queueUnavailable(jobKind));
        continue;
      }
      failures.reset();
      assertCurrent(sessionScope);

      // Result shape — the job is done and the outputs landed.
      if (response && !response.jobStatus) return response;

      // Active job — update the progress slot so the view can render a bar.
      if (response?.jobStatus === "pending" || response?.jobStatus === "running") {
        const current = state.aiJobs[jobKind] || {};
        state.aiJobs[jobKind] = {
          ...current,
          jobId: response.jobId || current.jobId,
          jobStatus: response.jobStatus,
          progress: response.progress || current.progress || null,
          language: response.language || current.language,
          tone: response.tone || current.tone,
        };
        continue;
      }
      if (response?.jobStatus === "error") throw new Error(response.error || `${jobKind} job failed.`);
      // not_found (or anything unexpected): the job is gone; the user can
      // start it again from the UI.
      throw new Error(`${jobKind} job not found. Try again.`);
    }
    throw new Error(`${jobKind} job timed out. Try again.`);
  } finally {
    if (isCurrent(sessionScope)) state.aiJobs[jobKind] = null;
  }
}

// Apply a finished result to the session on screen. The full workspace
// replaces it; an outputs-only result (AI job polls) is merged in.
function applyResult(sessionScope, response) {
  if (!response || typeof response !== "object" || !response.outputs) return false;
  if (!isCurrent(sessionScope)) return true;
  if (response.outputsOnly) {
    if (state.workspace?.sessionId === response.sessionId) {
      setWorkspace({ ...state.workspace, outputs: response.outputs });
    }
  } else {
    setWorkspace(response);
  }
  return true;
}

// ── Bootstrap, sign-out ───────────────────────────────────────────────────

function applyBootstrap(payload) {
  const defaultApiKey = String(payload?.defaults?.apiKey || "").trim();
  const connectedUser = payload?.auth?.connectedUser || null;
  const allowLocalApiKeyFallback = Boolean(payload?.auth?.allowLocalApiKeyFallback);
  state.auth.oauthEnabled = Boolean(payload?.auth?.oauthEnabled) || Boolean(connectedUser);
  state.auth.connectedUser = connectedUser;
  state.auth.allowLocalApiKeyFallback = allowLocalApiKeyFallback;
  state.auth.isAdmin = Boolean(payload?.auth?.isAdmin);
  if (defaultApiKey && (!state.apiKey || allowLocalApiKeyFallback)) {
    state.apiKey = defaultApiKey;
  }
  if (state.auth.oauthEnabled && !allowLocalApiKeyFallback) {
    state.apiKey = "";
  }
}

function resetWorkspace() {
  // Wipe every per-org/per-user slice. Called when the user disconnects
  // (or their session expires) so in-memory state never outlives the auth
  // context, and stop any work still running for the previous session.
  openSessionScope("");
  setWorkspace(null);
  state.workspaceSessions = [];
  state.workspaceSessionsLoadedAt = 0;
  state.error = "";
  state.transcriptUnavailableReason = "";
  state.transcriptJobProgress = null;
  state.aiJobs = emptyAiJobs();
  // Browser-side fetch state too — otherwise old event/session inputs
  // get carried across logins.
  state.sessionId = "";
  state.eventId = "";
  state.eventSessions = [];
  state.selectedEventSessionId = "";
  state.selectedWorkspaceEventId = "";
  state.loadedEventId = "";
  state.workspaceEvents = [];
  state.workspaceEventsNextPage = null;
}

// ── Search page (events, card list) ───────────────────────────────────────
//
// These lists belong to the signed-in account. A response that arrives after
// sign-out (or after another account signed in) is dropped.

function sameAccount(account) {
  return state.auth.connectedUser === account;
}

async function loadEventSessions() {
  if ((!state.apiKey && !state.auth.connectedUser) || !state.eventId) return;
  state.transcriptUnavailableReason = "";
  const normalizedEventId = state.eventId.trim();
  const currentSelection = state.selectedEventSessionId;
  const account = state.auth.connectedUser;
  const data = await wrapCall("eventSessions", () =>
    api.fetchEventSessions({
      apiKey: state.apiKey,
      eventId: normalizedEventId,
    })
  );
  if (!sameAccount(account)) return;
  state.eventSessions = data.options || [];
  state.loadedEventId = normalizedEventId;
  const selectionStillExists = state.eventSessions.some((session) => session.id === currentSelection);
  state.selectedEventSessionId =
    state.eventSessions.length === 1
      ? String(state.eventSessions[0]?.id || "")
      : selectionStillExists
        ? currentSelection
        : "";
}

async function loadWorkspaceSessions({ silent = false } = {}) {
  if (!state.auth.connectedUser && !state.auth.allowLocalApiKeyFallback) {
    // No OAuth and no local fallback — skip the network round-trip,
    // but DON'T stamp workspaceSessionsLoadedAt. Otherwise the view's
    // staleness check thinks the data is fresh and never re-triggers
    // when auth lands a moment later.
    state.workspaceSessions = [];
    return [];
  }

  const account = state.auth.connectedUser;
  if (silent) {
    // Background refresh after a fetch — keep the existing cards on
    // screen while we update so the user doesn't see a flash.
    try {
      const data = await api.fetchWorkspaceSessions();
      if (!sameAccount(account)) return state.workspaceSessions;
      state.workspaceSessions = Array.isArray(data?.sessions) ? data.sessions : [];
      state.workspaceSessionsLoadedAt = Date.now();
    } catch (error) {
      console.warn("Background refresh of the session list failed", error);
    }
    return state.workspaceSessions;
  }

  const data = await wrapCall("workspaceSessions", () => api.fetchWorkspaceSessions());
  if (!sameAccount(account)) return state.workspaceSessions;
  state.workspaceSessions = Array.isArray(data?.sessions) ? data.sessions : [];
  state.workspaceSessionsLoadedAt = Date.now();
  return state.workspaceSessions;
}

async function loadWorkspaceEvents() {
  if (!state.apiKey && !state.auth.connectedUser) return;
  state.transcriptUnavailableReason = "";
  const currentSelection = state.selectedWorkspaceEventId;
  const account = state.auth.connectedUser;
  const data = await wrapCall("workspaceEvents", () =>
    api.fetchWorkspaceEvents({
      apiKey: state.apiKey,
      pageNumber: 0,
      pageSize: 20,
      title: state.workspaceEventsTitle.trim(),
      schedulingStatus: state.workspaceEventsStatus.trim(),
    })
  );
  if (!sameAccount(account)) return;
  state.workspaceEvents = data.options || [];
  state.workspaceEventsNextPage =
    Number.isInteger(data?.nextPage) || typeof data?.nextPage === "number" ? data.nextPage : null;
  const selectionStillExists = state.workspaceEvents.some((event) => event.id === currentSelection);
  state.selectedWorkspaceEventId = selectionStillExists ? currentSelection : "";
}

async function loadMoreWorkspaceEvents() {
  if ((!state.apiKey && !state.auth.connectedUser) || state.workspaceEventsNextPage === null || state.workspaceEventsNextPage === undefined) return;
  state.transcriptUnavailableReason = "";
  const currentSelection = state.selectedWorkspaceEventId;
  const account = state.auth.connectedUser;
  const data = await wrapCall("workspaceEvents", () =>
    api.fetchWorkspaceEvents({
      apiKey: state.apiKey,
      pageNumber: state.workspaceEventsNextPage,
      pageSize: 20,
      title: state.workspaceEventsTitle.trim(),
      schedulingStatus: state.workspaceEventsStatus.trim(),
    })
  );
  if (!sameAccount(account)) return;
  const incoming = Array.isArray(data?.options) ? data.options : [];
  const merged = [...state.workspaceEvents];
  const seenIds = new Set(merged.map((event) => event.id));
  for (const event of incoming) {
    if (!event || seenIds.has(event.id)) continue;
    merged.push(event);
    seenIds.add(event.id);
  }
  state.workspaceEvents = merged;
  state.workspaceEventsNextPage =
    Number.isInteger(data?.nextPage) || typeof data?.nextPage === "number" ? data.nextPage : null;
  const selectionStillExists = state.workspaceEvents.some((event) => event.id === currentSelection);
  state.selectedWorkspaceEventId = selectionStillExists ? currentSelection : "";
}

async function loadSessionsForSelectedWorkspaceEvent() {
  if ((!state.apiKey && !state.auth.connectedUser) || !state.selectedWorkspaceEventId.trim()) return;
  state.inputMode = "event";
  state.eventId = state.selectedWorkspaceEventId.trim();
  state.loadedEventId = "";
  state.eventSessions = [];
  state.selectedEventSessionId = "";
  await loadEventSessions();
}

watch(
  () => [
    state.inputMode,
    state.sessionId,
    state.eventId,
    state.selectedEventSessionId,
    state.selectedWorkspaceEventId,
    state.workspaceEventsTitle,
    state.workspaceEventsStatus,
  ],
  (
    [inputMode, sessionId, eventId, selectedEventSessionId, selectedWorkspaceEventId, workspaceEventsTitle, workspaceEventsStatus],
    [previousMode, previousSessionId, previousEventId, previousSelectedEventSessionId, previousSelectedWorkspaceEventId, previousWorkspaceEventsTitle, previousWorkspaceEventsStatus] = [],
  ) => {
    const targetChanged =
      inputMode !== previousMode ||
      sessionId !== previousSessionId ||
      eventId !== previousEventId ||
      selectedEventSessionId !== previousSelectedEventSessionId ||
      selectedWorkspaceEventId !== previousSelectedWorkspaceEventId ||
      workspaceEventsTitle !== previousWorkspaceEventsTitle ||
      workspaceEventsStatus !== previousWorkspaceEventsStatus;

    if (!targetChanged) return;

    if (inputMode === "event" && eventId !== previousEventId) {
      state.eventSessions = [];
      state.loadedEventId = "";
      state.selectedEventSessionId = "";
      state.transcriptUnavailableReason = "";
    }

    if (selectedWorkspaceEventId !== previousSelectedWorkspaceEventId && inputMode !== "event") {
      state.eventSessions = [];
      state.loadedEventId = "";
      state.selectedEventSessionId = "";
      state.transcriptUnavailableReason = "";
    }

    if (workspaceEventsTitle !== previousWorkspaceEventsTitle || workspaceEventsStatus !== previousWorkspaceEventsStatus) {
      state.workspaceEvents = [];
      state.workspaceEventsNextPage = null;
      state.selectedWorkspaceEventId = "";
      state.eventSessions = [];
      state.loadedEventId = "";
      state.selectedEventSessionId = "";
      state.transcriptUnavailableReason = "";
    }
  }
);

// ── Loading a session ─────────────────────────────────────────────────────

// Start (or attach to) the transcript job and wait for it. Errors become
// the "transcript unavailable" reason unless `throwErrors` is set.
async function loadTranscript(sessionScope, { forceRefresh = false, throwErrors = false } = {}) {
  const { id, controller } = sessionScope;
  try {
    const response = await api.fetchSessionTranscript(
      id,
      { apiKey: state.apiKey, forceRefresh },
      { signal: controller.signal },
    );
    assertCurrent(sessionScope);
    // An async job started (large recording): poll until the worker finishes.
    const data = response?.jobStatus ? await pollTranscriptJob(sessionScope) : response;
    assertCurrent(sessionScope);
    if (data) setWorkspace(data);
    state.transcriptUnavailableReason = "";
    // The session (and its transcript state) shows up in the card list.
    loadWorkspaceSessions({ silent: true });
  } catch (error) {
    if (isStale(error) || throwErrors) throw error;
    state.transcriptUnavailableReason = getFriendlyTranscriptUnavailableMessage(errorMessage(error));
    loadWorkspaceSessions({ silent: true });
  }
}

// A cached session without a transcript: resume polling a running job, or
// surface why the last one failed. "not_found" means it was never started,
// so the view offers "Generate transcript".
async function resumeTranscript(sessionScope) {
  let jobStatus;
  try {
    jobStatus = await api.getTranscriptJobStatus(sessionScope.id, { signal: sessionScope.controller.signal });
  } catch (error) {
    if (isStale(error)) throw error;
    console.warn("Could not read the transcript job status", error);
    return;
  }
  assertCurrent(sessionScope);
  if (jobStatus?.jobStatus === "pending" || jobStatus?.jobStatus === "running") {
    try {
      const data = await pollTranscriptJob(sessionScope);
      assertCurrent(sessionScope);
      if (data) setWorkspace(data);
    } catch (error) {
      if (isStale(error)) throw error;
      state.transcriptUnavailableReason = getFriendlyTranscriptUnavailableMessage(errorMessage(error));
    }
  } else if (jobStatus?.jobStatus === "error") {
    state.transcriptUnavailableReason = getFriendlyTranscriptUnavailableMessage(
      jobStatus.error || "Transcript generation failed."
    );
  }
}

async function loadSessionById(sessionId) {
  // Load a specific session's workspace by ID. Used by the parameterised
  // session routes: the URL is the truth. Uses the cache when available;
  // otherwise fetches the base data and starts the transcript (a teammate
  // opening a shared link to a session not loaded in this browser yet).
  const id = String(sessionId || "").trim();
  if (!id) return null;

  // Already on screen, or already loading (Add session starts the load
  // before navigating, and App.vue's route watcher would start it again).
  if (scope.id === id && state.workspace?.sessionId === id) return state.workspace;
  if (scope.id === id && state.loading.sessionFetch) return null;

  const sessionScope = openSessionScope(id);
  const { signal } = sessionScope.controller;
  state.sessionId = id;
  state.transcriptUnavailableReason = "";
  state.transcriptJobProgress = null;
  state.aiJobs = emptyAiJobs();
  // Never render a different session's data while this one loads.
  if (state.workspace && state.workspace.sessionId !== id) setWorkspace(null);

  return wrapCall(
    "sessionFetch",
    async () => {
      let cached = null;
      try {
        cached = await api.getCachedSession(id, { signal });
      } catch (error) {
        if (isStale(error)) throw error;
        console.warn("Cache lookup failed; fetching the session instead", error);
      }
      assertCurrent(sessionScope);

      if (cached) {
        setWorkspace(cached);
        if (!workspaceHasTranscript(cached)) await resumeTranscript(sessionScope);
        return state.workspace;
      }

      // Not cached yet: the caller's Livestorm credentials must give access
      // to the session, otherwise the backend answers 4xx and the view
      // shows the error.
      state.inputMode = "session";
      const baseData = await api.fetchSessionBase(id, { apiKey: state.apiKey, forceRefresh: false }, { signal });
      assertCurrent(sessionScope);
      setWorkspace(baseData);
      await loadTranscript(sessionScope);
      return state.workspace;
    },
    sessionScope,
  );
}

async function retryTranscript() {
  const sessionScope = displayedSessionScope();
  if (!sessionScope) return;
  state.transcriptUnavailableReason = "";
  return wrapCall(
    "sessionFetch",
    () => loadTranscript(sessionScope, { forceRefresh: true, throwErrors: true }),
    sessionScope,
  );
}

async function saveSpeakerLabels(speakerNames) {
  const sessionScope = displayedSessionScope();
  if (!sessionScope) return;
  await wrapCall(
    "speakerLabels",
    async () => {
      const data = await api.saveSpeakerLabels(
        sessionScope.id,
        { apiKey: state.apiKey, speakerNames },
        { signal: sessionScope.controller.signal },
      );
      assertCurrent(sessionScope);
      setWorkspace(data);
    },
    sessionScope,
  );
}

// ── AI flows ──────────────────────────────────────────────────────────────
//
// Each POST returns either the full workspace (the output already exists)
// or a job to poll. Jobs always run against the session on screen.

async function runAiFlow({ flag, jobKind, flow, start, query, slot }) {
  const sessionScope = displayedSessionScope();
  if (!sessionScope) return;
  await wrapCall(
    flag,
    async () => {
      const { id, controller } = sessionScope;
      const started = await start(id, controller.signal);
      assertCurrent(sessionScope);
      if (applyResult(sessionScope, started)) return;
      state.aiJobs[jobKind] = { jobId: started.jobId, jobStatus: started.jobStatus || "pending", progress: null, ...slot };
      const final = await pollAiJob(sessionScope, jobKind, (signal) => api.getAiJobStatus(id, flow, query, { signal }));
      applyResult(sessionScope, final);
    },
    sessionScope,
  );
  return sessionScope;
}

async function runAnalysis(outputLanguage = state.outputLanguage) {
  await runAiFlow({
    flag: "analysis",
    jobKind: "overall_analysis",
    flow: "analysis",
    start: (id, signal) => api.runAnalysis(id, { apiKey: state.apiKey, outputLanguage }, { signal }),
    query: { language: outputLanguage },
    slot: { language: outputLanguage },
  });
}

async function runDeepAnalysis(outputLanguage = state.outputLanguage) {
  await runAiFlow({
    flag: "deepAnalysis",
    jobKind: "deep_analysis",
    flow: "deep-analysis",
    start: (id, signal) => api.runDeepAnalysis(id, { apiKey: state.apiKey, outputLanguage }, { signal }),
    query: { language: outputLanguage },
    slot: { language: outputLanguage },
  });
}

async function runSmartRecap(tone) {
  const sessionScope = await runAiFlow({
    flag: "smartRecap",
    jobKind: "smart_recap",
    flow: "smart-recap",
    start: (id, signal) => api.runSmartRecap(id, { apiKey: state.apiKey, tone }, { signal }),
    query: { tone },
    slot: { tone },
  });
  // The Professional recap makes the worker generate the cover image.
  // Watch for it quietly so the hero and the card list pick it up.
  if (sessionScope && String(tone || "").toLowerCase() === "professional") {
    watchForCoverImage(sessionScope);
  }
}

async function runContentRepurposing(outputLanguage = state.outputLanguage) {
  await runAiFlow({
    flag: "contentRepurposing",
    jobKind: "content_repurposing",
    flow: "content-repurposing",
    start: (id, signal) => api.runContentRepurposing(id, { apiKey: state.apiKey, outputLanguage }, { signal }),
    query: { language: outputLanguage },
    slot: { language: outputLanguage },
  });
}

// ── Cover image ───────────────────────────────────────────────────────────
//
// Checks the (small) session card list until the cover shows up, for at
// most COVER_WATCH_TIMEOUT_MS. Stops when the user opens another session.

const COVER_WATCH_INTERVAL_MS = 10_000;
const COVER_WATCH_TIMEOUT_MS = 3 * 60 * 1000;

async function watchForCoverImage(sessionScope) {
  if (sessionScope.watchingCover || !isCurrent(sessionScope)) return;
  sessionScope.watchingCover = true;
  const { id, controller } = sessionScope;
  const deadline = Date.now() + COVER_WATCH_TIMEOUT_MS;
  try {
    while (Date.now() < deadline) {
      await sleep(COVER_WATCH_INTERVAL_MS, controller.signal);
      let card;
      try {
        const data = await api.fetchWorkspaceSessions({ signal: controller.signal });
        assertCurrent(sessionScope);
        state.workspaceSessions = Array.isArray(data?.sessions) ? data.sessions : state.workspaceSessions;
        card = state.workspaceSessions.find((session) => session.sessionId === id);
      } catch (error) {
        if (isStale(error)) return;
        continue; // a hiccup: keep watching
      }
      if (card?.hasCoverImage) {
        if (state.workspace?.sessionId === id && state.workspace.meta) {
          setWorkspace({
            ...state.workspace,
            meta: {
              ...state.workspace.meta,
              hasCoverImage: true,
              coverImageGeneratedAt: card.coverImageGeneratedAt,
              coverImageMime: card.coverImageMime,
            },
          });
        }
        return;
      }
    }
  } catch (error) {
    if (!isStale(error)) console.warn("Cover image watch stopped", error);
  } finally {
    sessionScope.watchingCover = false;
  }
}

export function useWorkspace() {
  return {
    state,
    activeSessionId,
    hasTranscriptData,
    isTranscriptUnavailable,
    isTranscriptLoading,
    applyBootstrap,
    loadWorkspaceEvents,
    loadMoreWorkspaceEvents,
    loadSessionsForSelectedWorkspaceEvent,
    loadEventSessions,
    loadWorkspaceSessions,
    loadSessionById,
    retryTranscript,
    resetWorkspace,
    saveSpeakerLabels,
    runAnalysis,
    runDeepAnalysis,
    runSmartRecap,
    runContentRepurposing,
  };
}
