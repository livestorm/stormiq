// The v1 Report's data layer (src/report/useSessionReport.js) and its wording
// (src/report/format.js). The API is a fake passed in; timers are fake.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import record from "./fixtures/record-v1.json";
import { MAX_FAILURES, POLL_MS, createSessionReport } from "../src/report/useSessionReport";
import { analysisSummary, formatLength, formatSessionStart } from "../src/report/format";

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
    ...overrides,
  };
}

async function tick(ms) {
  await vi.advanceTimersByTimeAsync(ms);
}

describe("createSessionReport", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

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
    expect(report.state.analysis).toEqual({ status: "error", error: "The model declined this session." });
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
    expect(analysisSummary({ state: "ready", messages: 19 }, { status: "idle" })).toMatchObject({
      title: "Analysed", detail: "19 chat messages labelled.", action: "", tone: "good",
    });
    expect(analysisSummary({ state: "stale" }, { status: "completed" })).toMatchObject({ action: "Analyse again", tone: "fair" });
    expect(analysisSummary({ state: "ready" }, { status: "running" })).toMatchObject({ busy: true, action: "Analysing…" });
    expect(analysisSummary({ state: "none" }, { status: "error", error: "Boom" })).toMatchObject({
      detail: "Boom", action: "Try again", tone: "low",
    });
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
