import { describe, expect, it, vi } from "vitest";
import body from "./fixtures/chapters-v1.json";
import { emptyReason, noteRows, youtubeNote } from "../src/followup/chapters";
import { createChapters } from "../src/followup/useChapters";

describe("the chapters' words", () => {
  it("split the server's show notes into rows, in its order", () => {
    expect(noteRows(body)).toEqual([
      { time: "00:00", title: "Welcome and agenda" },
      { time: "01:00", title: "The dashboard, view by view" },
      { time: "04:48", title: "Pricing for larger teams" },
      { time: "12:40", title: "Exports and questions" },
      { time: "28:20", title: "Goodbye" },
    ]);
    expect(emptyReason(body)).toBe("");
  });

  it("say when YouTube wouldn't read them, as the server decided", () => {
    expect(body.showNotes.youtube).toBe(false);
    expect(youtubeNote(body)).toMatch(/at least three, each at least 10 seconds/);
    expect(youtubeNote({ ...body, showNotes: { ...body.showNotes, youtube: true } })).toBe("");
  });

  it("say why there are none", () => {
    const empty = { showNotes: { text: "", youtube: false }, chapters: [] };
    expect(emptyReason({ ...empty, state: "none" })).toMatch(/once the session is analysed/);
    expect(emptyReason({ ...empty, state: "stale" })).toMatch(/Analyse it again/);
    expect(emptyReason({ ...empty, state: "ready" })).toBe("The analysis found no chapters in this talk.");
    expect(youtubeNote({ ...empty, state: "ready" })).toBe("");
  });
});

function deferred() {
  let resolve;
  const promise = new Promise((done) => (resolve = done));
  return { promise, resolve };
}

describe("the chapters' data", () => {
  it("loads the chapters of the session it opens, with the scope's signal", async () => {
    const api = { getSessionChapters: vi.fn(async () => body) };
    const chapters = createChapters({ api });
    chapters.open("s1");
    await vi.waitFor(() => expect(chapters.state.body).toBe(body));
    expect(api.getSessionChapters).toHaveBeenCalledWith("s1", expect.objectContaining({ signal: expect.any(AbortSignal) }));
  });

  it("drops a late answer for a session it left, and aborts its request", async () => {
    const first = deferred();
    const other = { ...body, sessionId: "s2" };
    const api = { getSessionChapters: vi.fn((id) => (id === "s1" ? first.promise : Promise.resolve(other))) };
    const chapters = createChapters({ api });
    chapters.open("s1");
    const signal = api.getSessionChapters.mock.calls[0][1].signal;
    chapters.open("s2");
    expect(signal.aborted).toBe(true);
    await vi.waitFor(() => expect(chapters.state.body).toBe(other));
    first.resolve(body);
    await Promise.resolve();
    expect(chapters.state.body).toBe(other);
  });

  it("says why they failed to load, tries again on request, and stops on close", async () => {
    const api = {
      getSessionChapters: vi
        .fn()
        .mockRejectedValueOnce(Object.assign(new Error("The session's chapters could not be read."), { status: 500 }))
        .mockResolvedValueOnce(body),
    };
    const chapters = createChapters({ api });
    chapters.open("s1");
    await vi.waitFor(() => expect(chapters.state.error).toBe("The session's chapters could not be read."));
    chapters.reload();
    await vi.waitFor(() => expect(chapters.state.body).toBe(body));
    expect(chapters.state.error).toBe("");
    const signal = api.getSessionChapters.mock.calls[1][1].signal;
    chapters.close();
    expect(signal.aborted).toBe(true);
  });
});
