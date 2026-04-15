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
  getAnalysisPdfUrl(sessionId, kind, language) {
    const params = new URLSearchParams({
      kind,
      language,
    });
    return `/api/sessions/${sessionId}/analysis-pdf?${params.toString()}`;
  },
  runSmartRecap(sessionId, payload) {
    return request(`/api/sessions/${sessionId}/smart-recap`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  getSmartRecapPdfUrl(sessionId, tone) {
    const params = new URLSearchParams({ tone });
    return `/api/sessions/${sessionId}/smart-recap-pdf?${params.toString()}`;
  },
  runContentRepurposing(sessionId, payload) {
    return request(`/api/sessions/${sessionId}/content-repurposing`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  getContentRepurposingPdfUrl(sessionId, language, item) {
    const params = new URLSearchParams({
      language,
      item,
    });
    return `/api/sessions/${sessionId}/content-repurposing-pdf?${params.toString()}`;
  },
};
