// Store behaviour around async work: session switches, polling failures,
// loading flags and result merging. The API layer is mocked; timers are
// fake so polls run instantly.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { isReactive } from "vue";

const api = vi.hoisted(() => ({}));
vi.mock("../src/api.js", () => ({ api, onUnauthorized: () => {} }));

function workspace(sessionId, extra = {}) {
  return {
    sessionId,
    meta: { hasCoverImage: false },
    tables: { transcriptSegments: [{ text: `${sessionId} segment` }] },
    payloads: { transcript: { subtitles: [] } },
    text: { transcriptDisplay: `${sessionId} transcript` },
    outputs: { analysisBundle: {} },
    ...extra,
  };
}

function withoutTranscript(sessionId) {
  return workspace(sessionId, { tables: { transcriptSegments: [] }, payloads: { transcript: null }, text: { transcriptDisplay: "" } });
}

function httpError(status) {
  return Object.assign(new Error(`HTTP ${status}`), { status });
}

// Never settles until the signal aborts, like a slow real request.
function pendingUntilAborted(signal) {
  return new Promise((_resolve, reject) => {
    signal?.addEventListener("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })), { once: true });
  });
}

let store;

beforeEach(async () => {
  vi.useFakeTimers();
  for (const key of Object.keys(api)) delete api[key];
  Object.assign(api, {
    getCachedSession: vi.fn(async (id) => workspace(id)),
    getTranscriptJobStatus: vi.fn(async () => ({ jobStatus: "not_found" })),
    fetchSessionBase: vi.fn(),
    fetchSessionTranscript: vi.fn(),
    fetchWorkspaceSessions: vi.fn(async () => ({ sessions: [] })),
    runAnalysis: vi.fn(async () => ({ jobId: "overall_analysis:A:English", jobStatus: "pending" })),
    runSmartRecap: vi.fn(async () => ({ jobId: "smart_recap:A:professional", jobStatus: "pending" })),
    getAiJobStatus: vi.fn(async () => ({ jobStatus: "running" })),
    saveSpeakerLabels: vi.fn(),
  });
  vi.resetModules();
  const module = await import("../src/store/workspace.js");
  store = module.useWorkspace();
  store.state.auth.connectedUser = { email: "a@example.com" };
});

afterEach(() => {
  vi.useRealTimers();
});

describe("an event's sessions, a page at a time", () => {
  it("keeps the total and the cursor, and Show more adds the next page under the first", async () => {
    api.fetchEventSessions = vi.fn(async ({ cursor }) =>
      cursor
        ? { options: [{ id: "s2" }, { id: "s1" }], total: 3, next: null }
        : { options: [{ id: "s3" }, { id: "s2" }], total: 3, next: { page: 0, step: -1, pages: 2 } },
    );
    store.state.eventId = "e1";
    await store.loadEventSessions();
    expect(store.state.eventSessionsTotal).toBe(3);
    expect(store.state.eventSessionsNext).toEqual({ page: 0, step: -1, pages: 2 });
    await store.loadMoreEventSessions();
    expect(api.fetchEventSessions.mock.calls[1][0]).toMatchObject({ eventId: "e1", cursor: { page: 0, step: -1, pages: 2 } });
    expect(store.state.eventSessions.map((s) => s.id)).toEqual(["s3", "s2", "s1"]);
    expect(store.state.eventSessionsNext).toBeNull();
    await store.loadMoreEventSessions();
    expect(api.fetchEventSessions).toHaveBeenCalledTimes(2);
  });

  it("drops a page that arrives after another event was opened", async () => {
    let release;
    api.fetchEventSessions = vi.fn(({ cursor }) =>
      cursor ? new Promise((resolve) => (release = resolve)) : Promise.resolve({ options: [{ id: "a1" }], total: 2, next: { page: 1, step: 1, pages: 2 } }),
    );
    store.state.eventId = "e1";
    await store.loadEventSessions();
    const more = store.loadMoreEventSessions();
    store.state.loadedEventId = "e2";
    release({ options: [{ id: "a2" }], next: null });
    await more;
    expect(store.state.eventSessions.map((s) => s.id)).toEqual(["a1"]);
  });
});

describe("loading a session", () => {
  it("keeps the workspace out of Vue's deep reactivity", async () => {
    await store.loadSessionById("A");
    expect(store.state.workspace.sessionId).toBe("A");
    expect(isReactive(store.state.workspace)).toBe(false);
  });

  it("stops polling the old session's transcript when another session opens", async () => {
    api.getCachedSession.mockImplementation(async (id) => (id === "A" ? withoutTranscript("A") : workspace("B")));
    api.getTranscriptJobStatus.mockImplementation(async (id) => ({ jobId: `job-${id}`, jobStatus: "running", progress: { message: "…" } }));

    const loadingA = store.loadSessionById("A");
    await vi.advanceTimersByTimeAsync(13_000); // two polls for A
    expect(api.getTranscriptJobStatus.mock.calls.filter(([id]) => id === "A").length).toBeGreaterThanOrEqual(2);

    await store.loadSessionById("B");
    await loadingA;
    const pollsForA = api.getTranscriptJobStatus.mock.calls.filter(([id]) => id === "A").length;
    await vi.advanceTimersByTimeAsync(60_000);

    expect(api.getTranscriptJobStatus.mock.calls.filter(([id]) => id === "A").length).toBe(pollsForA);
    expect(store.state.workspace.sessionId).toBe("B");
    expect(store.state.transcriptJobProgress).toBeNull();
    expect(store.state.loading.sessionFetch).toBe(false);
  });

  it("drops a slow response for a session the user already left", async () => {
    let finishA;
    api.getCachedSession.mockImplementation((id) =>
      id === "A" ? new Promise((resolve) => { finishA = resolve; }) : Promise.resolve(workspace("B")),
    );
    const loadingA = store.loadSessionById("A");
    await store.loadSessionById("B");
    finishA(workspace("A"));
    await loadingA;
    expect(store.state.workspace.sessionId).toBe("B");
  });
});

describe("AI jobs", () => {
  it("run against the session on screen, not the search form's selection", async () => {
    store.state.inputMode = "event";
    store.state.selectedEventSessionId = "SOMETHING-ELSE";
    await store.loadSessionById("A");
    api.getAiJobStatus.mockResolvedValueOnce({ sessionId: "A", outputsOnly: true, outputs: { analysisBundle: { English: "# Done" } } });

    const run = store.runAnalysis("English");
    await vi.advanceTimersByTimeAsync(5_000);
    await run;

    expect(api.runAnalysis.mock.calls[0][0]).toBe("A");
    expect(api.getAiJobStatus.mock.calls[0].slice(0, 3)).toEqual(["A", "analysis", { language: "English" }]);
  });

  it("write the no-show email on its own route, job slot and loading flag", async () => {
    await store.loadSessionById("A");
    api.runNoShowFollowup = vi.fn(async () => ({ jobId: "no_show_followup:A:English", jobStatus: "pending" }));
    api.getAiJobStatus.mockResolvedValueOnce({ sessionId: "A", outputsOnly: true, outputs: { noShowFollowupBundle: { English: "Subject: Hi" } } });

    const run = store.runNoShowFollowup("English");
    expect(store.state.loading.noShowFollowup).toBe(true);
    await vi.advanceTimersByTimeAsync(5_000);
    await run;

    expect(api.runNoShowFollowup.mock.calls[0].slice(0, 2)).toEqual(["A", { apiKey: "", outputLanguage: "English" }]);
    expect(api.getAiJobStatus.mock.calls[0].slice(0, 3)).toEqual(["A", "no-show-followup", { language: "English" }]);
    expect(store.state.aiJobs).toHaveProperty("no_show_followup", null);
    expect(store.state.loading.noShowFollowup).toBe(false);
    expect(store.state.workspace.outputs.noShowFollowupBundle.English).toBe("Subject: Hi");
  });

  it("merge an outputs-only result into the workspace on screen", async () => {
    await store.loadSessionById("A");
    const tables = store.state.workspace.tables;
    api.getAiJobStatus.mockResolvedValueOnce({ sessionId: "A", outputsOnly: true, outputs: { analysisBundle: { English: "# Done" } } });

    const run = store.runAnalysis("English");
    await vi.advanceTimersByTimeAsync(5_000);
    await run;

    expect(store.state.workspace.outputs.analysisBundle.English).toBe("# Done");
    expect(store.state.workspace.tables).toBe(tables);
    expect(store.state.loading.analysis).toBe(false);
    expect(store.state.aiJobs.overall_analysis).toBeNull();
  });

  it("ride out a few transient poll failures", async () => {
    await store.loadSessionById("A");
    api.getAiJobStatus
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockRejectedValueOnce(httpError(502))
      .mockResolvedValueOnce({ jobStatus: "unknown" })
      .mockRejectedValueOnce(httpError(503))
      .mockResolvedValueOnce({ sessionId: "A", outputsOnly: true, outputs: { analysisBundle: { English: "# Done" } } });

    const run = store.runAnalysis("English");
    await vi.advanceTimersByTimeAsync(10 * 60_000);
    await run;

    expect(store.state.workspace.outputs.analysisBundle.English).toBe("# Done");
    expect(store.state.error).toBe("");
  });

  it("give up after five transient failures in a row", async () => {
    await store.loadSessionById("A");
    api.getAiJobStatus.mockRejectedValue(httpError(503));

    const run = store.runAnalysis("English").catch((error) => error);
    await vi.advanceTimersByTimeAsync(10 * 60_000);
    const error = await run;

    expect(error).toBeInstanceOf(Error);
    expect(api.getAiJobStatus).toHaveBeenCalledTimes(5);
    expect(store.state.error).toBe("HTTP 503");
    expect(store.state.loading.analysis).toBe(false);
  });

  it("stop at once on an error that won't go away", async () => {
    await store.loadSessionById("A");
    api.getAiJobStatus.mockRejectedValue(httpError(404));

    const run = store.runAnalysis("English").catch((error) => error);
    await vi.advanceTimersByTimeAsync(60_000);
    await run;

    expect(api.getAiJobStatus).toHaveBeenCalledTimes(1);
  });

  it("stop polling, and leave the loading flag alone, when the user opens another session", async () => {
    await store.loadSessionById("A");
    const runA = store.runAnalysis("English");
    await vi.advanceTimersByTimeAsync(9_000);
    expect(store.state.loading.analysis).toBe(true);

    await store.loadSessionById("B");
    expect(store.state.loading.analysis).toBe(false);

    api.runAnalysis.mockResolvedValueOnce({ jobId: "overall_analysis:B:English", jobStatus: "pending" });
    api.getAiJobStatus.mockImplementation((id, flow, query, { signal }) =>
      id === "B" ? pendingUntilAborted(signal) : Promise.resolve({ jobStatus: "running" }),
    );
    const runB = store.runAnalysis("English");
    await runA; // A's flow settles without touching B's state
    await vi.advanceTimersByTimeAsync(5_000);

    expect(store.state.loading.analysis).toBe(true);
    expect(api.getAiJobStatus.mock.calls.filter(([id]) => id === "A").length).toBeLessThanOrEqual(3);
    expect(store.state.error).toBe("");

    store.resetWorkspace();
    await runB;
    expect(store.state.loading.analysis).toBe(false);
  });

  it("never let an older flow's late finish clear a newer flow's loading flag", async () => {
    let releaseA;
    api.runAnalysis.mockImplementationOnce(
      () => new Promise((resolve) => { releaseA = () => resolve({ jobId: "overall_analysis:A:English", jobStatus: "pending" }); }),
    );
    await store.loadSessionById("A");
    const runA = store.runAnalysis("English"); // waits on a request that ignores the abort

    await store.loadSessionById("B");
    api.runAnalysis.mockResolvedValueOnce({ jobId: "overall_analysis:B:English", jobStatus: "pending" });
    api.getAiJobStatus.mockImplementation((id, flow, query, { signal }) => pendingUntilAborted(signal));
    const runB = store.runAnalysis("English");
    await vi.advanceTimersByTimeAsync(5_000);
    expect(store.state.loading.analysis).toBe(true);

    releaseA();
    await runA;
    expect(store.state.loading.analysis).toBe(true);
    expect(api.getAiJobStatus.mock.calls.filter(([id]) => id === "A")).toHaveLength(0);

    store.resetWorkspace();
    await runB;
  });

  async function finishProfessionalRecap() {
    await store.loadSessionById("A");
    api.getAiJobStatus.mockResolvedValueOnce({ sessionId: "A", outputsOnly: true, outputs: { smartRecapBundle: { professional: "# Recap" } } });
    const run = store.runSmartRecap("professional");
    await vi.advanceTimersByTimeAsync(5_000);
    await run;
  }

  it("pick up the cover generated after a Professional recap", async () => {
    await finishProfessionalRecap();
    api.fetchWorkspaceSessions.mockResolvedValueOnce({ sessions: [{ sessionId: "A", hasCoverImage: false }] });
    await vi.advanceTimersByTimeAsync(10_000);
    expect(store.state.workspace.meta.hasCoverImage).toBe(false);
    api.fetchWorkspaceSessions.mockResolvedValueOnce({
      sessions: [{ sessionId: "A", hasCoverImage: true, coverImageGeneratedAt: "2026-11-04T10:00:00Z", coverImageMime: "image/png" }],
    });
    await vi.advanceTimersByTimeAsync(10_000);
    expect(store.state.workspace.meta.hasCoverImage).toBe(true);
    expect(api.getCachedSession).toHaveBeenCalledTimes(1); // the watch never downloads the workspace
    const calls = api.fetchWorkspaceSessions.mock.calls.length;
    await vi.advanceTimersByTimeAsync(60_000);
    expect(api.fetchWorkspaceSessions.mock.calls.length).toBe(calls); // done once found
  });

  it("stop watching for the cover when the user opens another session", async () => {
    await finishProfessionalRecap();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(api.fetchWorkspaceSessions).toHaveBeenCalledTimes(1);

    await store.loadSessionById("B");
    await vi.advanceTimersByTimeAsync(5 * 60_000);
    expect(api.fetchWorkspaceSessions).toHaveBeenCalledTimes(1);
  });
});

describe("transcript poll and sign-out", () => {
  it("treat an 'unknown' transcript job status as a passing blip", async () => {
    api.getCachedSession.mockImplementation(async (id) => (id === "A" ? withoutTranscript("A") : workspace(id)));
    api.getTranscriptJobStatus
      .mockResolvedValueOnce({ jobId: "j", jobStatus: "running" }) // resume check
      .mockResolvedValueOnce({ jobStatus: "unknown" })
      .mockResolvedValueOnce({ jobStatus: "unknown" })
      .mockResolvedValueOnce(workspace("A")); // finished: the full workspace

    const loading = store.loadSessionById("A");
    await vi.advanceTimersByTimeAsync(5 * 60_000);
    await loading;

    expect(store.state.workspace.text.transcriptDisplay).toBe("A transcript");
    expect(store.state.transcriptUnavailableReason).toBe("");
  });

  it("give up on the transcript poll after five 'unknown' answers in a row", async () => {
    api.getCachedSession.mockImplementation(async (id) => (id === "A" ? withoutTranscript("A") : workspace(id)));
    api.getTranscriptJobStatus
      .mockResolvedValueOnce({ jobId: "j", jobStatus: "running" }) // resume check
      .mockResolvedValue({ jobStatus: "unknown" });

    const loading = store.loadSessionById("A");
    await vi.advanceTimersByTimeAsync(10 * 60_000);
    await loading;

    expect(api.getTranscriptJobStatus).toHaveBeenCalledTimes(6);
    expect(store.state.transcriptUnavailableReason).toMatch(/temporarily unavailable/);
  });

  it("never bring back the previous account's cards after sign-out", async () => {
    let finish;
    api.fetchWorkspaceSessions.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const loading = store.loadWorkspaceSessions();

    store.state.auth.connectedUser = null; // what App.vue does on sign-out / 401
    store.resetWorkspace();
    finish({ sessions: [{ sessionId: "old-org-session" }] });
    await loading;

    expect(store.state.workspaceSessions).toEqual([]);
  });
});

describe("the organization's switches", () => {
  it("show Surprise me only once the server says it is on", () => {
    expect(store.state.org.surpriseEnabled).toBe(false);
    store.applyBootstrap({ auth: {}, org: { surpriseEnabled: true } });
    expect(store.state.org.surpriseEnabled).toBe(true);
    store.applyBootstrap({ auth: {}, org: { surpriseEnabled: "yes" } });
    expect(store.state.org.surpriseEnabled).toBe(false);
    store.applyBootstrap({ auth: {} });
    expect(store.state.org.surpriseEnabled).toBe(false);
  });
});
