// The v1 Report's data layer (src/report/useSessionReport.js) and its wording
// (src/report/format.js). The API is a fake passed in; timers are fake.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import record from "./fixtures/record-v1.json";
import { MAX_FAILURES, POLL_MS, createSessionReport } from "../src/report/useSessionReport";
import { analysisSteps, analysisSummary, elapsedLabel, formatLength, formatSessionStart, preparationView } from "../src/report/format";

function httpError(status, message = `HTTP ${status}`) {
  const error = new Error(message);
  error.status = status;
  return error;
}

function fakeApi(overrides = {}) {
  return {
    getSessionRecord: vi.fn(async () => record),
    getSessionAnalysisJob: vi.fn(async () => ({ jobId: "j", jobStatus: "not_found" })),
    startSessionAnalysis: vi.fn(async () => ({ jobId: "j", jobStatus: "pending", enqueued: true })),
    getTranscriptJobStatus: vi.fn(async () => ({ jobStatus: "not_found" })),
    ...overrides,
  };
}

async function tick(ms) {
  await vi.advanceTimersByTimeAsync(ms);
}

describe("createSessionReport", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("prepares a session just added: the transcript, then the analysis the worker chains, then the Report", async () => {
    const transcript = ["running", "running", "completed"];
    const runs = ["not_found", "not_found", "pending", "running", "completed"];
    const api = fakeApi({
      getTranscriptJobStatus: vi.fn(async () => ({ jobStatus: transcript.shift() ?? "completed", progress: { message: "Transcribing…" } })),
      getSessionAnalysisJob: vi.fn(async () => ({ jobStatus: runs.shift() ?? "completed" })),
    });
    const report = createSessionReport({ api });
    await report.open("sess-1", { prepare: true });
    expect(report.state.preparing).toBe(true);
    expect(report.state.transcript).toMatchObject({ status: "running", progress: { message: "Transcribing…" } });
    await tick(POLL_MS * 2);
    expect(report.state.transcript.status).toBe("done");
    expect(api.getSessionRecord).toHaveBeenCalledTimes(2);
    for (let i = 0; i < 8 && report.state.preparing; i += 1) await tick(POLL_MS);
    expect(report.state.analysis.status).toBe("completed");
    expect(report.state.preparing).toBe(false);
    report.close();
  });

  it("waits for a transcript job that isn't queued yet, and ends when nothing comes", async () => {
    const api = fakeApi();
    const report = createSessionReport({ api });
    await report.open("sess-1", { prepare: true });
    expect(report.state.preparing).toBe(true);
    for (let i = 0; i < 12 && report.state.preparing; i += 1) await tick(POLL_MS);
    expect(report.state.preparing).toBe(false);
    expect(api.getTranscriptJobStatus.mock.calls.length).toBeGreaterThan(1);
    report.close();
  });

  it("never prepares a session opened normally", async () => {
    const api = fakeApi();
    const report = createSessionReport({ api });
    await report.open("sess-1");
    expect(report.state.preparing).toBe(false);
    expect(api.getTranscriptJobStatus).toHaveBeenCalledTimes(1);
    report.close();
  });

  it("loads the record and asks once whether a run is in flight", async () => {
    const api = fakeApi();
    const report = createSessionReport({ api });
    await report.open("sess-1");
    expect(report.state.record).toEqual(record);
    expect(report.state.loading).toBe(false);
    expect(report.state.analysis.status).toBe("idle");
    expect(api.getSessionRecord).toHaveBeenCalledWith("sess-1", expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(api.getSessionAnalysisJob).toHaveBeenCalledTimes(1);
  });

  it("follows a run a teammate started", async () => {
    const statuses = ["running", "completed"];
    const api = fakeApi({ getSessionAnalysisJob: vi.fn(async () => ({ jobStatus: statuses.shift() ?? "completed" })) });
    const report = createSessionReport({ api });
    await report.open("sess-1");
    expect(report.state.analysis.status).toBe("running");
    await tick(POLL_MS);
    expect(report.state.analysis.status).toBe("completed");
    expect(api.getSessionRecord).toHaveBeenCalledTimes(2);
  });

  it("starts the analysis, polls it and reloads the record when it is done", async () => {
    const statuses = ["not_found", "running", "running", "completed"];
    const api = fakeApi({ getSessionAnalysisJob: vi.fn(async () => ({ jobStatus: statuses.shift() })) });
    const report = createSessionReport({ api });
    await report.open("sess-1");
    await report.startAnalysis();
    expect(api.startSessionAnalysis).toHaveBeenCalledWith("sess-1", expect.any(Object));
    expect(report.state.analysis.status).toBe("pending");
    await tick(POLL_MS);
    expect(report.state.analysis.status).toBe("running");
    await tick(POLL_MS);
    await tick(POLL_MS);
    expect(report.state.analysis.status).toBe("completed");
    expect(api.getSessionRecord).toHaveBeenCalledTimes(2);
    await tick(POLL_MS * 5);
    expect(api.getSessionAnalysisJob).toHaveBeenCalledTimes(4);
  });

  it("keeps the run's progress while it is in flight", async () => {
    const progress = { kind: "session_analysis", stage: "reading", percent: 25, started_at_ms: 1000 };
    const api = fakeApi({ getSessionAnalysisJob: vi.fn(async () => ({ jobStatus: "running", progress })) });
    const report = createSessionReport({ api });
    await report.open("sess-1");
    expect(report.state.analysis).toEqual({ status: "running", error: "", progress });
    await tick(POLL_MS);
    expect(report.state.analysis.progress).toEqual(progress);
  });

  it("fetches the session again from Livestorm, then reads the record", async () => {
    const api = fakeApi({ fetchSessionBase: vi.fn(async () => ({})) });
    const report = createSessionReport({ api });
    await report.open("sess-refetch");
    expect(await report.refetch()).toBe(true);
    expect(api.fetchSessionBase).toHaveBeenCalledWith("sess-refetch", { apiKey: "", forceRefresh: true });
    await tick(0);
    expect(api.getSessionRecord).toHaveBeenCalledTimes(2);
    expect(report.state.refetch).toEqual({ status: "done", error: "" });
    api.fetchSessionBase.mockRejectedValueOnce(new Error("Livestorm is unreachable."));
    expect(await report.refetch()).toBe(false);
    await tick(0);
    expect(report.state.refetch).toEqual({ status: "error", error: "Livestorm is unreachable." });
    report.close();
  });

  it("shares a fetch again between the pages of a session: leaving one never cancels it", async () => {
    let release;
    const api = fakeApi({ fetchSessionBase: vi.fn(() => new Promise((resolve) => (release = resolve))) });
    const first = createSessionReport({ api });
    await first.open("sess-shared");
    const done = first.refetch();
    first.close(); // the user moves to another tab
    const second = createSessionReport({ api });
    await second.open("sess-shared");
    expect(second.state.refetch.status).toBe("running");
    release({});
    expect(await done).toBe(true);
    await tick(0);
    expect(second.state.refetch.status).toBe("done");
    expect(api.getSessionRecord).toHaveBeenCalledTimes(3); // the second page read the record again
    expect(api.fetchSessionBase).toHaveBeenCalledTimes(1);
    second.close();
  });

  it("ignores a second click while a run is in flight", async () => {
    const api = fakeApi({ getSessionAnalysisJob: vi.fn(async () => ({ jobStatus: "running" })) });
    const report = createSessionReport({ api });
    await report.open("sess-1");
    await report.startAnalysis();
    expect(api.startSessionAnalysis).not.toHaveBeenCalled();
  });

  it("shows the run's own error and lets the user start again", async () => {
    const statuses = ["not_found", "error"];
    const api = fakeApi({
      getSessionAnalysisJob: vi.fn(async () => ({ jobStatus: statuses.shift(), error: "The model declined this session." })),
    });
    const report = createSessionReport({ api });
    await report.open("sess-1");
    await report.startAnalysis();
    await tick(POLL_MS);
    expect(report.state.analysis).toMatchObject({ status: "error", error: "The model declined this session." });
    await report.startAnalysis();
    expect(api.startSessionAnalysis).toHaveBeenCalledTimes(2);
  });

  it("retries a blip with backoff and gives up after the limit", async () => {
    const api = fakeApi({
      getSessionAnalysisJob: vi
        .fn()
        .mockResolvedValueOnce({ jobStatus: "not_found" })
        .mockRejectedValue(httpError(503, "Service unavailable")),
    });
    const report = createSessionReport({ api });
    await report.open("sess-1");
    await report.startAnalysis();
    await tick(60_000 * 5);
    expect(api.getSessionAnalysisJob).toHaveBeenCalledTimes(1 + MAX_FAILURES);
    expect(report.state.analysis).toEqual({ status: "error", error: "Service unavailable" });
  });

  it("stops at once on an error that won't pass", async () => {
    const api = fakeApi({
      getSessionAnalysisJob: vi.fn().mockResolvedValueOnce({ jobStatus: "not_found" }).mockRejectedValue(httpError(404, "Gone")),
    });
    const report = createSessionReport({ api });
    await report.open("sess-1");
    await report.startAnalysis();
    await tick(POLL_MS);
    expect(report.state.analysis.status).toBe("error");
    await tick(POLL_MS * 10);
    expect(api.getSessionAnalysisJob).toHaveBeenCalledTimes(2);
  });

  it("drops a late answer from a session that was left", async () => {
    let resolveFirst;
    const api = fakeApi({
      getSessionRecord: vi
        .fn()
        .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }))
        .mockResolvedValue({ ...record, sessionId: "sess-2" }),
    });
    const report = createSessionReport({ api });
    const first = report.open("sess-1");
    await report.open("sess-2");
    resolveFirst(record);
    await first;
    expect(report.state.sessionId).toBe("sess-2");
    expect(report.state.record.sessionId).toBe("sess-2");
    expect(api.getSessionRecord.mock.calls[0][1].signal.aborted).toBe(true);
  });

  it("stops polling when closed", async () => {
    const api = fakeApi({ getSessionAnalysisJob: vi.fn(async () => ({ jobStatus: "running" })) });
    const report = createSessionReport({ api });
    await report.open("sess-1");
    report.close();
    await tick(POLL_MS * 10);
    expect(api.getSessionAnalysisJob).toHaveBeenCalledTimes(1);
  });

  it("says when the session can't be loaded", async () => {
    const api = fakeApi({ getSessionRecord: vi.fn(async () => { throw httpError(404, "Session not found"); }) });
    const report = createSessionReport({ api });
    await report.open("sess-1");
    expect(report.state).toMatchObject({ record: null, error: "Session not found", loading: false });
    expect(api.getSessionAnalysisJob).not.toHaveBeenCalled();
  });
});

describe("format", () => {
  it("dates a session in its own time zone, and in UTC when the zone is unknown", () => {
    const start = record.meta.clock.startedAtUnix;
    const paris = formatSessionStart(start, "Europe/Paris");
    expect(paris.zone).toBe("Europe/Paris");
    expect(paris.text).toMatch(/2026/);
    expect(formatSessionStart(start, "Mars/Olympus").zone).toBe("UTC");
    expect(formatSessionStart(null, "Europe/Paris")).toEqual({ text: "", zone: "" });
  });

  it("writes a length in minutes or hours", () => {
    expect(formatLength(1800)).toBe("30 min");
    expect(formatLength(5400)).toBe("1 h 30 min");
    expect(formatLength(7200)).toBe("2 h");
    expect(formatLength(null)).toBe("");
    expect(formatLength(0)).toBe("");
  });

  it("words the analysis panel from the labels' state and the run", () => {
    expect(analysisSummary({ state: "none" }, { status: "idle" })).toMatchObject({ action: "Analyse session", busy: false });
    // The chat is labelled but the session was never read, or was read from a session that changed since.
    expect(analysisSummary({ state: "ready", messages: 19 }, { status: "idle" }, { reading: "none" })).toMatchObject({
      action: "Analyse session",
      tone: "fair",
    });
    expect(analysisSummary({ state: "ready", messages: 19 }, { status: "idle" }, { reading: "stale" })).toMatchObject({ action: "Analyse again" });
    expect(analysisSummary({ state: "ready" }, { status: "idle" }, { reading: "none", transcript: false })).toMatchObject({ action: "", tone: "good" });
    expect(analysisSummary({ state: "ready" }, { status: "idle" }, { reading: "ready" })).toMatchObject({ action: "", tone: "good" });
    expect(analysisSummary({ state: "ready", messages: 19 }, { status: "idle" })).toMatchObject({
      title: "Analysed", detail: "19 chat messages labelled.", action: "", tone: "good",
    });
    expect(analysisSummary({ state: "stale" }, { status: "completed" })).toMatchObject({ action: "Analyse again", tone: "fair" });
    expect(analysisSummary({ state: "ready" }, { status: "running" })).toMatchObject({ busy: true, action: "Analysing…" });
    expect(analysisSummary({ state: "none" }, { status: "error", error: "Boom" })).toMatchObject({
      detail: "Boom", action: "Try again", tone: "low",
    });
  });

  it("words the preparation of a session just added", () => {
    const running = preparationView({ status: "running", progress: { message: "Uploading the recording" } }, { status: "idle" });
    expect(running.steps.map((step) => [step.label, step.state])).toEqual([
      ["Fetched from Livestorm", "done"],
      ["Transcribing the recording", "current"],
      ["Reading the chat and the talk with AI", "todo"],
      ["Your report", "todo"],
    ]);
    expect(running.steps[1].detail).toBe("Uploading the recording");
    const noRecording = preparationView({ status: "error" }, { status: "running", progress: { stage: "labelling" } });
    expect(noRecording.steps[1].state).toBe("skipped");
    expect(noRecording.steps[2]).toMatchObject({ state: "current", detail: "Labelling the chat. This takes about a minute." });
  });

  it("shows a run's steps from the stage the server sent", () => {
    const running = (progress) => analysisSummary({ state: "none" }, { status: "running", progress });
    const reading = running({ stage: "reading", percent: 25, started_at_ms: 1000 });
    expect(reading).toMatchObject({ percent: 25, startedAtMs: 1000 });
    expect(reading.detail).toMatch(/^Reading the transcript/);
    expect(reading.steps).toEqual([
      { label: "Label the chat", state: "done" },
      { label: "Read the transcript", state: "current" },
    ]);
    expect(running(null)).toMatchObject({ percent: null, startedAtMs: null, detail: "Labelling the chat. This takes about a minute." });
    expect(running(null).steps.map((step) => step.state)).toEqual(["current", "todo"]);
    // A queued run shows no stage: what the server holds may be the last run's.
    const queued = analysisSummary({ state: "none" }, { status: "pending", progress: { stage: "done", percent: 100 } });
    expect(queued).toMatchObject({ detail: "Waiting for a free worker.", percent: null });
    expect(queued.steps.map((step) => step.state)).toEqual(["todo", "todo"]);
    expect(analysisSteps("labelling", { transcript: false })).toEqual([{ label: "Label the chat", state: "current" }]);
    expect(analysisSteps("done").map((step) => step.state)).toEqual(["done", "done"]);
  });

  it("words the time a run has taken", () => {
    expect(elapsedLabel(1000, 66_000)).toBe("1:05");
    expect(elapsedLabel(0, 3_725_000)).toBe("1:02:05");
    expect(elapsedLabel(5000, 1000)).toBe("0:00");
    expect(elapsedLabel(null, 1000)).toBe("");
  });
});

describe("loadChat", () => {
  const chat = { sessionId: "sess-1", updatedAt: record.updatedAt, messages: [{ id: "C0001", t: 12, text: "Hello", team: false }] };

  it("reads the chat once, by id, with the session's signal", async () => {
    const api = fakeApi({ getSessionChat: vi.fn(async () => chat) });
    const report = createSessionReport({ api });
    await report.open("sess-1");
    expect(report.state.chat.status).toBe("idle");
    await report.loadChat();
    expect(api.getSessionChat).toHaveBeenCalledWith("sess-1", expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(report.state.chat.status).toBe("ready");
    expect(report.state.chat.byId.C0001.text).toBe("Hello");
    await report.loadChat();
    expect(api.getSessionChat).toHaveBeenCalledTimes(1);
  });

  it("keeps a failure retryable", async () => {
    const answers = [Promise.reject(httpError(500, "The session's chat could not be read.")), Promise.resolve(chat)];
    const api = fakeApi({ getSessionChat: vi.fn(() => answers.shift()) });
    const report = createSessionReport({ api });
    await report.open("sess-1");
    await report.loadChat();
    expect(report.state.chat).toMatchObject({ status: "error", error: "The session's chat could not be read." });
    await report.loadChat();
    expect(report.state.chat.status).toBe("ready");
  });

  it("drops a late chat after another session opened", async () => {
    let release;
    const api = fakeApi({ getSessionChat: vi.fn(() => new Promise((resolve) => (release = resolve))) });
    const report = createSessionReport({ api });
    await report.open("sess-1");
    const pending = report.loadChat();
    await report.open("sess-2");
    release(chat);
    await pending;
    expect(report.state.chat.status).toBe("idle");
    expect(report.state.chat.byId).toEqual({});
  });

  it("drops the chat when a reloaded record has a new updatedAt", async () => {
    const records = [record, { ...record, updatedAt: "2030-01-01T00:00:00+00:00" }];
    const statuses = ["running", "completed"];
    const api = fakeApi({
      getSessionRecord: vi.fn(async () => records.shift() ?? record),
      getSessionAnalysisJob: vi.fn(async () => ({ jobStatus: statuses.shift() ?? "not_found" })),
      getSessionChat: vi.fn(async () => chat),
    });
    vi.useFakeTimers();
    const report = createSessionReport({ api });
    await report.open("sess-1");
    await report.loadChat();
    expect(report.state.chat.status).toBe("ready");
    await vi.advanceTimersByTimeAsync(POLL_MS);
    expect(api.getSessionRecord).toHaveBeenCalledTimes(2);
    expect(report.state.chat.status).toBe("idle");
    report.close();
    vi.useRealTimers();
  });
});

describe("a session just added, still being fetched", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("waits for its record while the fetch runs, then goes on", async () => {
    const answers = [httpError(404), httpError(404)];
    const api = fakeApi({
      getSessionRecord: vi.fn(async () => {
        const next = answers.shift();
        if (next) throw next;
        return record;
      }),
    });
    const report = createSessionReport({ api });
    const opening = report.open("sess-1", { prepare: true });
    await tick(0);
    expect(report.state.fetching).toBe(true);
    expect(report.state.error).toBe("", "no error while it waits");
    await tick(POLL_MS * 2);
    await opening;
    expect(report.state.fetching).toBe(false);
    expect(report.state.record).toEqual(record);
    expect(report.state.preparing).toBe(true);
    report.close();
  });

  it("stops waiting and says why when the fetch fails", async () => {
    const api = fakeApi({ getSessionRecord: vi.fn(async () => Promise.reject(httpError(404))) });
    const report = createSessionReport({ api });
    const opening = report.open("sess-1", { prepare: true });
    await tick(0);
    report.fetchFailed("This session hasn't taken place yet, so there is nothing to analyse.");
    await tick(POLL_MS);
    await opening;
    const calls = api.getSessionRecord.mock.calls.length;
    await tick(POLL_MS * 3);
    expect(api.getSessionRecord.mock.calls.length).toBe(calls);
    expect(report.state).toMatchObject({
      fetching: false,
      preparing: false,
      error: "This session hasn't taken place yet, so there is nothing to analyse.",
    });
    report.close();
  });

  it("shows the fetch as the current step, and how far it is", () => {
    const fetching = preparationView({ status: "idle" }, { status: "idle" }, { fetching: true });
    expect(fetching.steps.map((step) => step.state)).toEqual(["current", "todo", "todo", "todo"]);
    expect(fetching.percent).toBe(3);
    expect(preparationView({ status: "running", progress: { percent: 45 } }, { status: "idle" }).percent).toBe(37);
    expect(preparationView({ status: "done" }, { status: "running", progress: { percent: 40 } }).percent).toBe(80);
    expect(preparationView({ status: "done" }, { status: "completed" }).percent).toBe(100);
    expect(preparationView({ status: "running", progress: { percent: "x" } }, { status: "idle" }).percent).toBe(10);
  });
});
