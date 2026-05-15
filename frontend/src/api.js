function getFriendlyErrorMessage(path, response, errorPayload) {
  const backendMessage =
    errorPayload?.detail?.message ||
    errorPayload?.detail?.details?.message ||
    response.statusText;

  const isSessionLookup =
    response.status === 404 &&
    (path === "/api/event-sessions" || /\/api\/sessions\/[^/]+\/fetch$/.test(path));

  if (isSessionLookup) {
    return "Resource not found (HTTP 404). Please verify the provided Session ID/Event ID exists in your Livestorm workspace.";
  }

  return backendMessage;
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  if (!response.ok) {
    let errorPayload = null;
    try {
      errorPayload = await response.json();
    } catch (error) {
      errorPayload = { detail: { message: response.statusText } };
    }
    const message = getFriendlyErrorMessage(path, response, errorPayload);
    const error = new Error(message);
    error.status = response.status;
    error.payload = errorPayload;
    throw error;
  }

  if (response.status === 204) {
    return null;
  }

  return response.json();
}

export const api = {
  health() {
    return request("/api/health");
  },
  bootstrap() {
    return request("/api/bootstrap");
  },
  logout() {
    return request("/api/auth/logout", {
      method: "POST",
    });
  },
  fetchWorkspaceEvents(payload) {
    return request("/api/workspace-events", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  fetchEventSessions(payload) {
    return request("/api/event-sessions", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  // Phase 4: list all cached sessions for the current user's Livestorm
  // organization. Powers the Single Analysis card grid. Returns
  // { sessions: [] } when the user isn't OAuth-connected — the view
  // shows a "connect with Livestorm" empty state in that case.
  fetchWorkspaceSessions() {
    return request("/api/workspace-sessions");
  },
  fetchSession(sessionId, payload) {
    return request(`/api/sessions/${sessionId}/fetch`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  fetchSessionBase(sessionId, payload) {
    return request(`/api/sessions/${sessionId}/fetch-base`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  fetchSessionTranscript(sessionId, payload) {
    return request(`/api/sessions/${sessionId}/fetch-transcript`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  getCachedSession(sessionId) {
    return request(`/api/sessions/${sessionId}/cached`);
  },
  getTranscriptJobStatus(sessionId) {
    return request(`/api/sessions/${sessionId}/transcript-job`);
  },
  getSession(sessionId) {
    return request(`/api/sessions/${sessionId}`);
  },
  saveSpeakerLabels(sessionId, payload) {
    return request(`/api/sessions/${sessionId}/speaker-labels`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  // ── AI flow start (POST) ────────────────────────────────────────────────
  //
  // Phase 2 contract: each POST returns EITHER the full serialised
  // workspace (cache hit — bundle already exists for the requested
  // language/tone) OR { jobId, jobKind, jobStatus: 'pending', language|tone }
  // when a worker job has been enqueued. The store decides which path to
  // take by inspecting `result?.jobId` — present means "poll", absent
  // means "apply immediately."
  runAnalysis(sessionId, payload) {
    return request(`/api/sessions/${sessionId}/analysis`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  runDeepAnalysis(sessionId, payload) {
    return request(`/api/sessions/${sessionId}/deep-analysis`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  runSmartRecap(sessionId, payload) {
    return request(`/api/sessions/${sessionId}/smart-recap`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  runContentRepurposing(sessionId, payload) {
    return request(`/api/sessions/${sessionId}/content-repurposing`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  // ── AI flow polling (GET) ───────────────────────────────────────────────
  //
  // Polled every 4-6s while a job is in flight. Returns the full
  // serialised workspace when the bundle has landed in session_cache;
  // otherwise { jobId, jobKind, jobStatus, progress, error? }.
  getAnalysisJobStatus(sessionId, jobId, language) {
    const params = new URLSearchParams({ jobId: jobId || "", language: language || "English" });
    return request(`/api/sessions/${sessionId}/analysis/job?${params.toString()}`);
  },
  getDeepAnalysisJobStatus(sessionId, jobId, language) {
    const params = new URLSearchParams({ jobId: jobId || "", language: language || "English" });
    return request(`/api/sessions/${sessionId}/deep-analysis/job?${params.toString()}`);
  },
  getSmartRecapJobStatus(sessionId, jobId, tone) {
    const params = new URLSearchParams({ jobId: jobId || "", tone: tone || "professional" });
    return request(`/api/sessions/${sessionId}/smart-recap/job?${params.toString()}`);
  },
  getContentRepurposingJobStatus(sessionId, jobId, language) {
    const params = new URLSearchParams({ jobId: jobId || "", language: language || "English" });
    return request(`/api/sessions/${sessionId}/content-repurposing/job?${params.toString()}`);
  },
  // ── PDF download URLs (unchanged) ───────────────────────────────────────
  getAnalysisPdfUrl(sessionId, kind, language) {
    const params = new URLSearchParams({
      kind,
      language,
    });
    return `/api/sessions/${sessionId}/analysis-pdf?${params.toString()}`;
  },
  getSmartRecapPdfUrl(sessionId, tone) {
    const params = new URLSearchParams({ tone });
    return `/api/sessions/${sessionId}/smart-recap-pdf?${params.toString()}`;
  },
  getContentRepurposingPdfUrl(sessionId, language, item) {
    const params = new URLSearchParams({
      language,
      item,
    });
    return `/api/sessions/${sessionId}/content-repurposing-pdf?${params.toString()}`;
  },

  // ── Admin ─────────────────────────────────────────────────────────────────
  adminGetUsers() {
    return request("/api/admin/users");
  },
  adminGetSessions() {
    return request("/api/admin/sessions");
  },
  adminPromoteUser(email, user_id = "") {
    return request("/api/admin/users/promote", {
      method: "POST",
      body: JSON.stringify({ email, user_id }),
    });
  },
  adminDemoteUser(email) {
    return request("/api/admin/users/demote", {
      method: "POST",
      body: JSON.stringify({ email, user_id: "" }),
    });
  },
  adminDeleteSession(sessionId) {
    return request(`/api/admin/sessions/${sessionId}`, { method: "DELETE" });
  },
};
