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

// Called whenever the server answers 401 (the Livestorm session expired or
// was revoked). App.vue registers it to reset the signed-in state and send
// the user to the connect screen; each request still rejects as usual.
let unauthorizedHandler = null;

export function onUnauthorized(handler) {
  unauthorizedHandler = typeof handler === "function" ? handler : null;
}

async function request(path, options = {}) {
  const { headers, ...rest } = options;
  const response = await fetch(path, {
    ...rest,
    headers: {
      "Content-Type": "application/json",
      ...(headers || {}),
    },
  });

  if (!response.ok) {
    let errorPayload = null;
    try {
      errorPayload = await response.json();
    } catch {
      errorPayload = { detail: { message: response.statusText } };
    }
    const message = getFriendlyErrorMessage(path, response, errorPayload);
    const error = new Error(message);
    error.status = response.status;
    error.payload = errorPayload;
    if (response.status === 401 && unauthorizedHandler && !path.startsWith("/api/auth/")) {
      unauthorizedHandler(error);
    }
    throw error;
  }

  if (response.status === 204) {
    return null;
  }

  return response.json();
}

// Session ids come from URLs and form fields: always encode them.
function sessionPath(sessionId, suffix = "") {
  return `/api/sessions/${encodeURIComponent(String(sessionId ?? ""))}${suffix}`;
}

// Session-scoped calls accept `{ signal }` so the store can cancel them when
// the user moves to another session.
function postJson(path, payload, { signal } = {}) {
  return request(path, { method: "POST", body: JSON.stringify(payload), signal });
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
    return postJson("/api/workspace-events", payload);
  },
  fetchEventSessions(payload) {
    return postJson("/api/event-sessions", payload);
  },
  // List all cached sessions for the current user's Livestorm
  // organization. Powers the Single Analysis card grid. Returns
  // { sessions: [] } when the user isn't OAuth-connected — the view
  // shows a "connect with Livestorm" empty state in that case.
  fetchWorkspaceSessions({ signal } = {}) {
    return request("/api/workspace-sessions", { signal });
  },
  fetchSessionBase(sessionId, payload, options) {
    return postJson(sessionPath(sessionId, "/fetch-base"), payload, options);
  },
  fetchSessionTranscript(sessionId, payload, options) {
    return postJson(sessionPath(sessionId, "/fetch-transcript"), payload, options);
  },
  getCachedSession(sessionId, { signal } = {}) {
    return request(sessionPath(sessionId, "/cached"), { signal });
  },
  getTranscriptJobStatus(sessionId, { signal } = {}) {
    return request(sessionPath(sessionId, "/transcript-job"), { signal });
  },
  getSession(sessionId, { signal } = {}) {
    return request(sessionPath(sessionId), { signal });
  },
  saveSpeakerLabels(sessionId, payload, options) {
    return postJson(sessionPath(sessionId, "/speaker-labels"), payload, options);
  },
  // ── AI flow start (POST) ────────────────────────────────────────────────
  //
  // Each POST returns EITHER the full serialised workspace (cache hit —
  // the bundle already exists for the requested language/tone) OR
  // { jobId, jobKind, jobStatus, language|tone } when a worker job runs.
  runAnalysis(sessionId, payload, options) {
    return postJson(sessionPath(sessionId, "/analysis"), payload, options);
  },
  runFaq(sessionId, payload, options) {
    return postJson(sessionPath(sessionId, "/faq"), payload, options);
  },
  runDecisions(sessionId, payload, options) {
    return postJson(sessionPath(sessionId, "/decisions"), payload, options);
  },
  runDeepAnalysis(sessionId, payload, options) {
    return postJson(sessionPath(sessionId, "/deep-analysis"), payload, options);
  },
  runSmartRecap(sessionId, payload, options) {
    return postJson(sessionPath(sessionId, "/smart-recap"), payload, options);
  },
  runContentRepurposing(sessionId, payload, options) {
    return postJson(sessionPath(sessionId, "/content-repurposing"), payload, options);
  },
  // ── AI flow polling (GET) ───────────────────────────────────────────────
  //
  // Polled every few seconds while a job is in flight. Returns
  // { jobId, jobKind, jobStatus, progress, error? } until the result is
  // saved, then { sessionId, outputsOnly: true, outputs } (we ask for the
  // AI outputs only: the rest of the workspace is already on screen).
  getAiJobStatus(sessionId, flow, query, { signal } = {}) {
    const params = new URLSearchParams({ ...query, outputsOnly: "true" });
    return request(sessionPath(sessionId, `/${flow}/job?${params.toString()}`), { signal });
  },
  // ── The v1 Report ───────────────────────────────────────────────────────
  //
  // The session's record (numbers computed by the server, docs/api.md "The
  // v1 record"), and the session analysis the user starts (the chat labels,
  // for now). The analysis poll takes no job id: the server derives it.
  getSessionRecord(sessionId, { signal } = {}) {
    return request(sessionPath(sessionId, "/record"), { signal });
  },
  getSessionChat(sessionId, { signal } = {}) {
    return request(sessionPath(sessionId, "/record/chat"), { signal });
  },
  // The Transcript tab's room view: speech, chat and questions as text by id.
  getSessionTranscriptView(sessionId, { signal } = {}) {
    return request(sessionPath(sessionId, "/record/transcript"), { signal });
  },
  // The Follow-up tab's question desk: every audience question and its status.
  getSessionQuestionDesk(sessionId, { signal } = {}) {
    return request(sessionPath(sessionId, "/record/questions"), { signal });
  },
  // Before you share: the figures said on stage to check, with the host's answers.
  getFigureChecks(sessionId, { signal } = {}) {
    return request(sessionPath(sessionId, "/record/figures"), { signal });
  },
  // Answers one figure check (`choice` null reopens it); returns the checks.
  saveFigureCheck(sessionId, { key, choice }, { signal } = {}) {
    return request(sessionPath(sessionId, "/record/figures"), {
      method: "PUT",
      body: JSON.stringify({ key, choice: choice ?? null }),
      signal,
    });
  },
  // The chapters the session analysis found, with their show notes.
  getSessionChapters(sessionId, { signal } = {}) {
    return request(sessionPath(sessionId, "/record/chapters"), { signal });
  },
  // The Report's findings: what stood out, what was checked, what couldn't be.
  getSessionFindings(sessionId, { signal } = {}) {
    return request(sessionPath(sessionId, "/record/findings"), { signal });
  },
  // The Pulse's key moments, told: what was said, what people wrote, the questions.
  getSessionMoments(sessionId, { signal } = {}) {
    return request(sessionPath(sessionId, "/record/moments"), { signal });
  },
  // The Report's "Who spoke up": the audience members who wrote, with one message each.
  getSessionVoices(sessionId, { signal } = {}) {
    return request(sessionPath(sessionId, "/record/voices"), { signal });
  },
  // Marks one desk question answered (or not), with an optional reply; returns the desk.
  saveSessionQuestion(sessionId, { questionId, answered, reply = "" }, { signal } = {}) {
    return request(sessionPath(sessionId, "/record/questions"), {
      method: "PUT",
      body: JSON.stringify({ questionId, answered: Boolean(answered), reply: String(reply ?? "") }),
      signal,
    });
  },
  // The host's decision on an answer located in the talk ("confirm" or
  // "reject"); returns the desk.
  decideLocatedAnswer(sessionId, { questionId, decision }, { signal } = {}) {
    return request(sessionPath(sessionId, "/record/questions/located"), {
      method: "PUT",
      body: JSON.stringify({ questionId, decision }),
      signal,
    });
  },
  startSessionAnalysis(sessionId, { signal } = {}) {
    return request(sessionPath(sessionId, "/session-analysis"), { method: "POST", signal });
  },
  getSessionAnalysisJob(sessionId, { signal } = {}) {
    return request(sessionPath(sessionId, "/session-analysis/job"), { signal });
  },
  // ── Download URLs ───────────────────────────────────────────────────────
  getAnalysisPdfUrl(sessionId, kind, language) {
    const params = new URLSearchParams({ kind, language });
    return `${sessionPath(sessionId, "/analysis-pdf")}?${params.toString()}`;
  },
  getSmartRecapPdfUrl(sessionId, tone) {
    const params = new URLSearchParams({ tone });
    return `${sessionPath(sessionId, "/smart-recap-pdf")}?${params.toString()}`;
  },
  getContentRepurposingPdfUrl(sessionId, language, item) {
    const params = new URLSearchParams({ language, item });
    return `${sessionPath(sessionId, "/content-repurposing-pdf")}?${params.toString()}`;
  },
  // The open questions with who asked them (name, company; no email), as CSV.
  // The question desk's CSV: `scope` "open" (the questions still open),
  // "all" (with each one's status) or "answers" (with its status and answer).
  getQuestionsCsvUrl(sessionId, scope = "open") {
    return `${sessionPath(sessionId, "/record/questions.csv")}?scope=${encodeURIComponent(scope)}`;
  },
  // Full raw transcript; the workspace payload only carries what the UI renders.
  getTranscriptJsonUrl(sessionId) {
    return sessionPath(sessionId, "/transcript.json");
  },

  // ── Admin ─────────────────────────────────────────────────────────────────
  adminGetUsers() {
    return request("/api/admin/users");
  },
  adminGetSessions() {
    return request("/api/admin/sessions");
  },
  adminPromoteUser(email, user_id = "") {
    return postJson("/api/admin/users/promote", { email, user_id });
  },
  adminDemoteUser(email) {
    return postJson("/api/admin/users/demote", { email, user_id: "" });
  },
  adminDeleteSession(sessionId) {
    return request(`/api/admin/sessions/${encodeURIComponent(String(sessionId ?? ""))}`, { method: "DELETE" });
  },
  adminGetSettings() {
    return request("/api/admin/settings");
  },
  adminUpdateSetting(key, value) {
    return postJson("/api/admin/settings", { key, value });
  },
};
