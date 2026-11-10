import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import body from "./fixtures/transcript-v1.json";
import { MAX_FAILURES, POLL_MS, createTranscriptView } from "../src/transcript/useTranscript";

const empty = { ...body, speech: [], speakers: [], clock: { interleave: false, offsetS: null } };

function fakeApi(overrides = {}) {
  return {
    getSessionTranscriptView: vi.fn(async () => body),
    getTranscriptJobStatus: vi.fn(async () => ({ jobStatus: "not_found" })),
    fetchSessionTranscript: vi.fn(async () => ({ jobStatus: "pending" })),
    saveSpeakerLabels: vi.fn(async () => ({})),
    ...overrides,
  };
}

async function flush(ms = 0) {
  await vi.advanceTimersByTimeAsync(ms);
}

describe("the Transcript tab's data", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("loads the view and asks nothing about the job when there is speech", async () => {
    const api = fakeApi();
    const view = createTranscriptView({ api });
    view.open("s1");
    await flush();
    expect(view.state.body).toBe(body);
    expect(view.state.loading).toBe(false);
    expect(api.getTranscriptJobStatus).not.toHaveBeenCalled();
    expect(api.fetchSessionTranscript).not.toHaveBeenCalled();
  });

  it("without speech, says the transcript was never made and starts nothing by itself", async () => {
    const api = fakeApi({ getSessionTranscriptView: vi.fn(async () => empty) });
    const view = createTranscriptView({ api });
    view.open("s1");
    await flush(POLL_MS * 4);
    expect(view.state.job.status).toBe("none");
    expect(api.fetchSessionTranscript).not.toHaveBeenCalled();
  });

  it("follows a running transcript, then shows it", async () => {
    const statuses = [{ jobStatus: "running", progress: { percent: 40.4 } }, { jobStatus: "running" }, { sessionId: "s1" }];
    const views = [empty, body];
    const api = fakeApi({
      getSessionTranscriptView: vi.fn(async () => views.shift() ?? body),
      getTranscriptJobStatus: vi.fn(async () => statuses.shift()),
    });
    const view = createTranscriptView({ api });
    view.open("s1");
    await flush();
    expect(view.state.job).toEqual({ status: "running", error: "", progress: 40 });
    await flush(POLL_MS);
    expect(view.state.job.status).toBe("running");
    await flush(POLL_MS);
    expect(view.state.body).toBe(body);
    expect(view.state.job.status).toBe("idle");
  });

  it("keeps polling through blips and gives up after too many", async () => {
    const blip = Object.assign(new Error("Bad gateway"), { status: 502 });
    let calls = 0;
    const api = fakeApi({
      getSessionTranscriptView: vi.fn(async () => empty),
      getTranscriptJobStatus: vi.fn(async () => {
        calls += 1;
        if (calls === 1) return { jobStatus: "pending" };
        throw blip;
      }),
    });
    const view = createTranscriptView({ api });
    view.open("s1");
    await flush();
    await flush(10 * 60 * 1000);
    expect(api.getTranscriptJobStatus).toHaveBeenCalledTimes(1 + MAX_FAILURES);
    expect(view.state.job.status).toBe("error");
  });

  it("shows a failed transcript and starts it again only when asked, forced", async () => {
    const api = fakeApi({
      getSessionTranscriptView: vi.fn(async () => empty),
      getTranscriptJobStatus: vi.fn(async () => ({ jobStatus: "error", error: "The recording is missing." })),
    });
    const view = createTranscriptView({ api });
    view.open("s1");
    await flush();
    expect(view.state.job).toEqual({ status: "error", error: "The recording is missing." });
    expect(api.fetchSessionTranscript).not.toHaveBeenCalled();
    await view.makeTranscript({ force: true });
    expect(api.fetchSessionTranscript).toHaveBeenCalledTimes(1);
    expect(api.fetchSessionTranscript.mock.calls[0][1]).toEqual({ apiKey: "", forceRefresh: true });
    expect(view.state.job.status).toBe("pending");
    await view.makeTranscript();
    expect(api.fetchSessionTranscript).toHaveBeenCalledTimes(1);
  });

  it("drops a late answer for a session no longer open", async () => {
    let resolve;
    const api = fakeApi({ getSessionTranscriptView: vi.fn(() => new Promise((done) => (resolve = done))) });
    const view = createTranscriptView({ api });
    view.open("s1");
    const signal = api.getSessionTranscriptView.mock.calls[0][1].signal;
    view.close();
    expect(signal.aborted).toBe(true);
    resolve(body);
    await flush();
    expect(view.state.body).toBe(null);
  });

  it("saves the speakers' names and reloads", async () => {
    const api = fakeApi();
    const view = createTranscriptView({ api });
    view.open("s1");
    await flush();
    await view.saveNames({ 0: "Nora", 2: "Ilan" });
    expect(api.saveSpeakerLabels.mock.calls[0][1]).toEqual({ apiKey: "", speakerNames: { 0: "Nora", 2: "Ilan" } });
    expect(api.getSessionTranscriptView).toHaveBeenCalledTimes(2);
    expect(view.state.saving).toBe(false);
  });

  it("says when the names could not be saved", async () => {
    const api = fakeApi({ saveSpeakerLabels: vi.fn(async () => Promise.reject(new Error("Session not found"))) });
    const view = createTranscriptView({ api });
    view.open("s1");
    await flush();
    await view.saveNames({ 0: "Nora" });
    expect(view.state.saveError).toBe("Session not found");
  });
});
