import { describe, expect, it, vi } from "vitest";
import body from "./fixtures/questions-v1.json";
import {
  FILTERS,
  canMark,
  chatNotice,
  filterCounts,
  filterQuestions,
  logisticsLine,
  metaLine,
  statusOf,
  summaryLine,
} from "../src/followup/desk";
import { createQuestionDesk } from "../src/followup/useQuestionDesk";

const byText = Object.fromEntries(body.questions.map((q) => [q.text, q]));

describe("the question desk's words", () => {
  it("count each filter from the server's summary, and filter in its order", () => {
    expect(FILTERS.map((f) => f.key)).toEqual(["open", "answered", "all"]);
    expect(filterCounts(body.summary)).toEqual({ open: 2, answered: 3, all: 5 });
    expect(filterQuestions(body.questions, "open").map((q) => q.text)).toEqual(["What about pricing?", "Is there an API?"]);
    expect(filterQuestions(body.questions, "answered")).toHaveLength(3);
    expect(filterQuestions(body.questions, "all")).toBe(body.questions);
  });

  it("sum the desk up, leaving out the statuses nobody has", () => {
    expect(summaryLine(body.summary)).toBe("5 questions: 2 still open · 2 answered in writing · 1 answered aloud");
    expect(summaryLine({ total: 1, open: 0, here: 1 })).toBe("1 question: 1 answered here");
    expect(summaryLine({ total: 0 })).toBe("No audience questions in the Q&A or the chat.");
    expect(logisticsLine(body.summary)).toBe("1 replay or slides request in the chat, counted apart.");
    expect(logisticsLine({ logistics: 3 })).toBe("3 replay or slides requests in the chat, counted apart.");
    expect(logisticsLine({})).toBe("");
  });

  it("say why chat questions are missing", () => {
    expect(chatNotice("ready")).toBe("");
    expect(chatNotice("none")).toMatch(/once the session is analysed/);
    expect(chatNotice("stale")).toMatch(/changed since it was analysed/);
  });

  it("word each question's status and line, and let the host mark only open or own answers", () => {
    expect(statusOf(byText["Will the slides be shared?"])).toEqual({ status: "measured", label: "Answered in writing" });
    expect(statusOf(byText["Is there an API?"])).toEqual({ status: "check", label: "Still open" });
    expect(statusOf({ status: "here" }).label).toBe("Answered here");
    expect(metaLine(byText["Will the slides be shared?"])).toBe("Theo P. · Q&A · Before");
    expect(metaLine(byText["What about pricing?"])).toBe("Sam R. · Chat · 3:20");
    expect(metaLine({ author: "Attendee", source: "qa", t: null })).toBe("Attendee · Q&A");
    expect(canMark(byText["Is there an API?"])).toBe(true);
    expect(canMark(byText["Will the slides be shared?"])).toBe(false);
    expect(canMark({ status: "here", canSave: true })).toBe(true);
    expect(canMark({ status: "open", canSave: false })).toBe(false);
  });
});

function fakeApi(overrides = {}) {
  return {
    getSessionQuestionDesk: vi.fn(async () => body),
    saveSessionQuestion: vi.fn(async () => body),
    ...overrides,
  };
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => (resolve = done));
  return { promise, resolve };
}

describe("the question desk's data", () => {
  it("loads the desk of the session it opens", async () => {
    const api = fakeApi();
    const desk = createQuestionDesk({ api });
    desk.open("s1");
    await vi.waitFor(() => expect(desk.state.body).toBe(body));
    expect(desk.state.loading).toBe(false);
    expect(api.getSessionQuestionDesk).toHaveBeenCalledWith("s1", expect.objectContaining({ signal: expect.any(AbortSignal) }));
  });

  it("saves a mark and shows the desk the server sends back", async () => {
    const marked = { ...body, summary: { ...body.summary, open: 1, here: 1 } };
    const api = fakeApi({ saveSessionQuestion: vi.fn(async () => marked) });
    const desk = createQuestionDesk({ api });
    desk.open("s1");
    await vi.waitFor(() => expect(desk.state.body).toBe(body));
    const ok = await desk.mark("Q004", { answered: true, reply: "See the docs." });
    expect(ok).toBe(true);
    expect(api.saveSessionQuestion).toHaveBeenCalledWith(
      "s1",
      { questionId: "Q004", answered: true, reply: "See the docs." },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(desk.state.body).toBe(marked);
    expect(desk.state.saving).toBe("");
  });

  it("keeps the desk and says why when a mark fails", async () => {
    const error = Object.assign(new Error("No such question in this session's question desk."), { status: 404 });
    const api = fakeApi({ saveSessionQuestion: vi.fn(async () => Promise.reject(error)) });
    const desk = createQuestionDesk({ api });
    desk.open("s1");
    await vi.waitFor(() => expect(desk.state.body).toBe(body));
    expect(await desk.mark("Q004", { answered: true })).toBe(false);
    expect(desk.state.body).toBe(body);
    expect(desk.state.saveError).toEqual({ id: "Q004", message: error.message });
  });

  it("drops a late answer for a session it left, and aborts its request", async () => {
    const first = deferred();
    const other = { ...body, sessionId: "s2" };
    const api = fakeApi({
      getSessionQuestionDesk: vi.fn((id) => (id === "s1" ? first.promise : Promise.resolve(other))),
    });
    const desk = createQuestionDesk({ api });
    desk.open("s1");
    const signal = api.getSessionQuestionDesk.mock.calls[0][1].signal;
    desk.open("s2");
    expect(signal.aborted).toBe(true);
    await vi.waitFor(() => expect(desk.state.body).toBe(other));
    first.resolve(body);
    await Promise.resolve();
    expect(desk.state.body).toBe(other);
    expect(desk.state.sessionId).toBe("s2");
  });

  it("drops a mark that answers after the session changed", async () => {
    const save = deferred();
    const api = fakeApi({ saveSessionQuestion: vi.fn(() => save.promise) });
    const desk = createQuestionDesk({ api });
    desk.open("s1");
    await vi.waitFor(() => expect(desk.state.body).toBe(body));
    const pending = desk.mark("Q004", { answered: true });
    desk.open("s2");
    save.resolve({ ...body, sessionId: "s1-late" });
    expect(await pending).toBe(false);
    expect(desk.state.body?.sessionId).not.toBe("s1-late");
  });

  it("says why the desk failed to load, and tries again on request", async () => {
    const api = fakeApi({
      getSessionQuestionDesk: vi
        .fn()
        .mockRejectedValueOnce(Object.assign(new Error("The session's questions could not be read."), { status: 500 }))
        .mockResolvedValueOnce(body),
    });
    const desk = createQuestionDesk({ api });
    desk.open("s1");
    await vi.waitFor(() => expect(desk.state.error).toBe("The session's questions could not be read."));
    desk.reload();
    await vi.waitFor(() => expect(desk.state.body).toBe(body));
    expect(desk.state.error).toBe("");
  });

  it("stops everything on close", async () => {
    const api = fakeApi();
    const desk = createQuestionDesk({ api });
    desk.open("s1");
    const signal = api.getSessionQuestionDesk.mock.calls[0][1].signal;
    desk.close();
    expect(signal.aborted).toBe(true);
    expect(await desk.mark("Q004", { answered: true })).toBe(false);
    expect(api.saveSessionQuestion).not.toHaveBeenCalled();
  });
});
