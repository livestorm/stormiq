import { computed, reactive, watch } from "vue";
import { api } from "../api";

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
  workspace: null,
  transcriptUnavailableReason: "",
  transcriptJobProgress: null,
  loading: {
    workspaceEvents: false,
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
  // Cleared to null when the job lands in the cache (the workspace
  // refresh replaces state.workspace and the view's "done" check fires).
  // `transcript` slot mirrors the AI flows so all loading-state UIs use
  // the same <AiJobProgress> component.
  aiJobs: {
    transcript: null,
    overall_analysis: null,
    deep_analysis: null,
    smart_recap: null,
    content_repurposing: null,
  },
  error: "",
});

const activeSessionId = computed(() =>
  state.inputMode === "session" ? state.sessionId.trim() : state.selectedEventSessionId.trim()
);

const hasTranscriptData = computed(() => {
  const payload = state.workspace?.payloads?.transcript;
  const segments = state.workspace?.tables?.transcriptSegments || [];
  const text = String(state.workspace?.text?.transcriptDisplay || "").trim();
  return Boolean(payload) || segments.length > 0 || Boolean(text);
});

const isTranscriptUnavailable = computed(
  () => Boolean(state.workspace) && !hasTranscriptData.value && Boolean(String(state.transcriptUnavailableReason || "").trim())
);

const isTranscriptLoading = computed(
  () => Boolean(state.workspace) && state.loading.sessionFetch && !hasTranscriptData.value && !isTranscriptUnavailable.value
);

function getFriendlyTranscriptUnavailableMessage(message) {
  const normalized = String(message || "").toLowerCase();
  if (normalized.includes("no mp4 video recording found")) {
    return "Transcript isn’t available for this session because Livestorm does not expose a usable MP4 recording for it. Session Overview and Chat & Questions can still work, but Transcript, Analysis, Repurposing, and Smart Recap require a video recording.";
  }
  return String(message || "").trim();
}

async function pollTranscriptJob(sessionId) {
  const POLL_INTERVAL_MS = 6000;
  const POLL_TIMEOUT_MS = 90 * 60 * 1000; // 90 minutes — enough for very long recordings
  const deadline = Date.now() + POLL_TIMEOUT_MS;

  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
    const status = await api.getTranscriptJobStatus(sessionId);
    // When the job finishes the backend returns the full workspace payload
    // (no jobStatus field), so we can use it directly.
    if (!status?.jobStatus) {
      state.transcriptJobProgress = null;
      state.aiJobs.transcript = null;
      return status;
    }
    // Legacy DB shape (Gladia step + message) — kept for the existing
    // "Transcript still loading" copy in the AI views.
    if (status.progress) {
      state.transcriptJobProgress = status.progress;
    }
    // New Redis stage-floor shape — feeds <AiJobProgress>.
    if (status.progressRedis) {
      state.aiJobs.transcript = {
        jobId: status.jobId,
        jobStatus: status.jobStatus,
        progress: status.progressRedis,
      };
    }
    if (status.jobStatus === "completed") {
      state.transcriptJobProgress = null;
      state.aiJobs.transcript = null;
      const cached = await api.getCachedSession(sessionId);
      if (cached) return cached;
    }
    if (status.jobStatus === "error") {
      state.transcriptJobProgress = null;
      state.aiJobs.transcript = null;
      throw new Error(status.error || "Transcript generation failed.");
    }
  }
  state.transcriptJobProgress = null;
  state.aiJobs.transcript = null;
  throw new Error("Transcript generation timed out. Please try refreshing the page later.");
}

async function wrapCall(flag, fn) {
  state.error = "";
  state.loading[flag] = true;
  try {
    return await fn();
  } catch (error) {
    state.error = error instanceof Error ? error.message : String(error);
    throw error;
  } finally {
    state.loading[flag] = false;
  }
}

function applyBootstrap(payload) {
  const defaultApiKey = String(payload?.defaults?.apiKey || "").trim();
  const connectedUser = payload?.auth?.connectedUser || null;
  const allowLocalApiKeyFallback = Boolean(payload?.auth?.allowLocalApiKeyFallback);
  state.auth.oauthEnabled = Boolean(payload?.auth?.oauthEnabled) || Boolean(connectedUser);
  state.auth.connectedUser = connectedUser;
  state.auth.allowLocalApiKeyFallback = allowLocalApiKeyFallback;
  if (defaultApiKey && (!state.apiKey || allowLocalApiKeyFallback)) {
    state.apiKey = defaultApiKey;
  }
  if (state.auth.oauthEnabled && !allowLocalApiKeyFallback) {
    state.apiKey = "";
    return;
  }
}

function resetWorkspace() {
  state.workspace = null;
  state.error = "";
  state.transcriptUnavailableReason = "";
}

async function loadEventSessions() {
  if ((!state.apiKey && !state.auth.connectedUser) || !state.eventId) return;
  state.transcriptUnavailableReason = "";
  const normalizedEventId = state.eventId.trim();
  const currentSelection = state.selectedEventSessionId;
  const data = await wrapCall("eventSessions", () =>
    api.fetchEventSessions({
      apiKey: state.apiKey,
      eventId: normalizedEventId,
    })
  );
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

async function loadWorkspaceEvents() {
  if (!state.apiKey && !state.auth.connectedUser) return;
  state.transcriptUnavailableReason = "";
  const currentSelection = state.selectedWorkspaceEventId;
  const data = await wrapCall("workspaceEvents", () =>
    api.fetchWorkspaceEvents({
      apiKey: state.apiKey,
      pageNumber: 0,
      pageSize: 20,
      title: state.workspaceEventsTitle.trim(),
      schedulingStatus: state.workspaceEventsStatus.trim(),
    })
  );
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
  const data = await wrapCall("workspaceEvents", () =>
    api.fetchWorkspaceEvents({
      apiKey: state.apiKey,
      pageNumber: state.workspaceEventsNextPage,
      pageSize: 20,
      title: state.workspaceEventsTitle.trim(),
      schedulingStatus: state.workspaceEventsStatus.trim(),
    })
  );
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

async function fetchSessionData(forceRefresh = false) {
  return wrapCall("sessionFetch", async () => {
    state.transcriptUnavailableReason = "";
    state.transcriptJobProgress = null;

    if (state.inputMode === "event") {
      const normalizedEventId = state.eventId.trim();
      const shouldReloadEventSessions =
        !state.eventSessions.length || state.loadedEventId !== normalizedEventId;

      if (shouldReloadEventSessions) {
        await loadEventSessions();
        return null;
      }

      if (!state.selectedEventSessionId.trim()) {
        state.error = "Please select a session from the dropdown to continue.";
        return null;
      }
    }

    if (!activeSessionId.value) return;

    if (!forceRefresh) {
      const cached = await api.getCachedSession(activeSessionId.value);
      if (cached) {
        state.workspace = cached;
        state.transcriptUnavailableReason = "";
        return cached;
      }
    }

    const baseData = await api.fetchSessionBase(activeSessionId.value, {
      apiKey: state.apiKey,
      forceRefresh,
    });
    state.workspace = baseData;

    try {
      const transcriptResponse = await api.fetchSessionTranscript(activeSessionId.value, {
        apiKey: state.apiKey,
        forceRefresh,
      });
      // Async job started (large recording) — poll until the background worker finishes.
      const transcriptData = transcriptResponse?.jobStatus
        ? await pollTranscriptJob(activeSessionId.value)
        : transcriptResponse;
      state.workspace = transcriptData;
      state.transcriptUnavailableReason = "";
      return transcriptData;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      state.transcriptUnavailableReason = getFriendlyTranscriptUnavailableMessage(message);
      state.error = state.transcriptUnavailableReason;
      return baseData;
    }
  });
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

async function saveSpeakerLabels(speakerNames) {
  if (!activeSessionId.value) return;
  const data = await wrapCall("speakerLabels", () =>
    api.saveSpeakerLabels(activeSessionId.value, {
      apiKey: state.apiKey,
      speakerNames,
    })
  );
  state.workspace = data;
}

// ── AI job polling ─────────────────────────────────────────────────────────
//
// Phase 2 contract: each AI-flow POST returns either the full serialised
// workspace (cache hit) or { jobId, jobKind, jobStatus, language|tone }.
// When we get a jobId, we poll the matching /job endpoint every 4s until
// it returns the workspace (job done) or an error state.
//
// The polling shape is uniform across all four flows, so this helper
// handles all of them. The caller passes a `pollFn` that already knows
// how to call the right endpoint with the right params (language / tone).

const AI_POLL_INTERVAL_MS = 4000;
const AI_POLL_TIMEOUT_MS = 30 * 60 * 1000; // 30 minutes — well above any legitimate AI flow runtime

async function pollAiJob(jobKind, pollFn) {
  const deadline = Date.now() + AI_POLL_TIMEOUT_MS;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, AI_POLL_INTERVAL_MS));
    let response;
    try {
      response = await pollFn();
    } catch (error) {
      state.aiJobs[jobKind] = null;
      throw error;
    }

    // Workspace shape — job is done and the bundle landed. The server
    // returns the full serialised workspace; replace state in one step.
    if (response && !response.jobStatus) {
      state.aiJobs[jobKind] = null;
      return response;
    }

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

    // Terminal failure — surface the error and stop polling.
    if (response?.jobStatus === "error") {
      state.aiJobs[jobKind] = null;
      throw new Error(response.error || `${jobKind} job failed.`);
    }

    // not_found / unknown / anything else — stop polling rather than
    // burn cycles. The user can re-trigger from the UI.
    if (response?.jobStatus === "not_found" || response?.jobStatus === "unknown") {
      state.aiJobs[jobKind] = null;
      throw new Error(`${jobKind} job not found. Try again.`);
    }
  }
  state.aiJobs[jobKind] = null;
  throw new Error(`${jobKind} job timed out. Try again.`);
}

function applyWorkspaceIfPresent(response) {
  // Both POST and polling endpoints return the full serialised workspace
  // when the result is in cache. Detect that shape (presence of `outputs`
  // is a reliable marker — job-status responses have no `outputs` key).
  if (response && typeof response === "object" && response.outputs) {
    state.workspace = response;
    return true;
  }
  return false;
}

async function runAnalysis(outputLanguage = state.outputLanguage) {
  if (!activeSessionId.value) return;
  await wrapCall("analysis", async () => {
    const start = await api.runAnalysis(activeSessionId.value, {
      apiKey: state.apiKey,
      outputLanguage,
    });
    if (applyWorkspaceIfPresent(start)) return;
    state.aiJobs.overall_analysis = {
      jobId: start.jobId,
      jobStatus: start.jobStatus || "pending",
      progress: null,
      language: outputLanguage,
    };
    const final = await pollAiJob("overall_analysis", () =>
      api.getAnalysisJobStatus(activeSessionId.value, start.jobId, outputLanguage),
    );
    applyWorkspaceIfPresent(final);
  });
}

async function runDeepAnalysis(outputLanguage = state.outputLanguage) {
  if (!activeSessionId.value) return;
  await wrapCall("deepAnalysis", async () => {
    const start = await api.runDeepAnalysis(activeSessionId.value, {
      apiKey: state.apiKey,
      outputLanguage,
    });
    if (applyWorkspaceIfPresent(start)) return;
    state.aiJobs.deep_analysis = {
      jobId: start.jobId,
      jobStatus: start.jobStatus || "pending",
      progress: null,
      language: outputLanguage,
    };
    const final = await pollAiJob("deep_analysis", () =>
      api.getDeepAnalysisJobStatus(activeSessionId.value, start.jobId, outputLanguage),
    );
    applyWorkspaceIfPresent(final);
  });
}

async function runSmartRecap(tone) {
  if (!activeSessionId.value) return;
  await wrapCall("smartRecap", async () => {
    const start = await api.runSmartRecap(activeSessionId.value, {
      apiKey: state.apiKey,
      tone,
    });
    if (applyWorkspaceIfPresent(start)) return;
    state.aiJobs.smart_recap = {
      jobId: start.jobId,
      jobStatus: start.jobStatus || "pending",
      progress: null,
      tone,
    };
    const final = await pollAiJob("smart_recap", () =>
      api.getSmartRecapJobStatus(activeSessionId.value, start.jobId, tone),
    );
    applyWorkspaceIfPresent(final);
  });
}

async function runContentRepurposing(outputLanguage = state.outputLanguage) {
  if (!activeSessionId.value) return;
  await wrapCall("contentRepurposing", async () => {
    const start = await api.runContentRepurposing(activeSessionId.value, {
      apiKey: state.apiKey,
      outputLanguage,
    });
    if (applyWorkspaceIfPresent(start)) return;
    state.aiJobs.content_repurposing = {
      jobId: start.jobId,
      jobStatus: start.jobStatus || "pending",
      progress: null,
      language: outputLanguage,
    };
    const final = await pollAiJob("content_repurposing", () =>
      api.getContentRepurposingJobStatus(activeSessionId.value, start.jobId, outputLanguage),
    );
    applyWorkspaceIfPresent(final);
  });
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
    fetchSessionData,
    resetWorkspace,
    saveSpeakerLabels,
    runAnalysis,
    runDeepAnalysis,
    runSmartRecap,
    runContentRepurposing,
  };
}
