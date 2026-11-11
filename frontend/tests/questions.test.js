import { describe, expect, it, vi } from "vitest";
import body from "./fixtures/questions-v1.json";
import {
  FILTERS,
  canMark,
  chatNotice,
  csvChoices,
  filterCounts,
  filterQuestions,
  locatedActions,
  locatedLabel,
  locatedNotice,
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
    expect(body.summary).toMatchObject({ open: 1, covered: 1 });
    expect(filterQuestions(body.questions, "open").map((q) => q.text)).toEqual(["What about pricing?", "Is there an API?"]);
    expect(filterQuestions(body.questions, "answered")).toHaveLength(3);
    expect(filterQuestions(body.questions, "all")).toBe(body.questions);
  });

  it("sum the desk up, leaving out the statuses nobody has", () => {
    expect(summaryLine(body.summary)).toBe(
      "5 questions: 1 still open · 1 covered in the talk, not marked · 2 answered in writing · 1 answered aloud",
    );
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

  it("show a passage of the talk that answers a question, and what the host can do with it", () => {
    const pricing = byText["What about pricing?"];
    const aloud = byText["How does pricing work for larger teams with several hundred seats?"];
    expect(statusOf(pricing)).toEqual({ status: "estimated", label: "Covered in the talk, not marked" });
    expect(pricing.located).toEqual({ t: 318.9, quote: "Pricing works per team and scales with seats." });
    expect(locatedLabel(pricing)).toBe("Seems answered in the talk at 5:19");
    expect(locatedLabel(aloud)).toBe("Answered in the talk at 5:19");
    expect(locatedLabel(byText["Is there an API?"])).toBe("");
    expect(locatedActions(pricing)).toEqual({ confirm: true, reject: true });
    expect(locatedActions(aloud)).toEqual({ confirm: false, reject: true });
    expect(locatedActions({ ...pricing, status: "here" })).toEqual({ confirm: false, reject: false });
    expect(locatedActions({ ...pricing, canSave: false })).toEqual({ confirm: false, reject: false });
  });

  it("say when answers from the talk are out of date, once", () => {
    expect(locatedNotice(body.summary)).toBe("");
    expect(locatedNotice({ located: "stale", chatQuestions: "ready" })).toMatch(/Analyse it again/);
    expect(locatedNotice({ located: "stale", chatQuestions: "stale" })).toBe("", "the chat's notice already says it");
    expect(locatedNotice({ located: "none" })).toBe("");
  });
});

function fakeApi(overrides = {}) {
  return {
    getSessionQuestionDesk: vi.fn(async () => body),
    saveSessionQuestion: vi.fn(async () => body),
    decideLocatedAnswer: vi.fn(async () => body),
    ...overrides,
  };
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => (resolve = done));
  return { promise, resolve };
}

describe("the question desk's download", () => {
  it("offers the open questions, all of them, or all with their answers", () => {
    expect(csvChoices({ open: 2, all: 5 }).map((choice) => [choice.scope, choice.label, choice.disabled])).toEqual([
      ["open", "Open questions", false],
      ["all", "All questions", false],
      ["answers", "All questions with answers", false],
    ]);
    expect(csvChoices({ open: 0, all: 5 })[0].disabled).toBe(true);
    expect(csvChoices({}).every((choice) => choice.disabled)).toBe(true);
  });

  it("asks the server for one scope at a time", async () => {
    const { api } = await import("../src/api");
    expect(api.getQuestionsCsvUrl("s 1", "answers")).toBe("/api/sessions/s%201/record/questions.csv?scope=answers");
    expect(api.getQuestionsCsvUrl("s1")).toBe("/api/sessions/s1/record/questions.csv?scope=open");
  });
});

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

  it("saves the host's decision on a located answer", async () => {
    const confirmed = { ...body, summary: { ...body.summary, covered: 0, here: 1 } };
    const api = fakeApi({ decideLocatedAnswer: vi.fn(async () => confirmed) });
    const desk = createQuestionDesk({ api });
    desk.open("s1");
    await vi.waitFor(() => expect(desk.state.body).toBe(body));
    expect(await desk.decide("C0010", "confirm")).toBe(true);
    expect(api.decideLocatedAnswer).toHaveBeenCalledWith(
      "s1",
      { questionId: "C0010", decision: "confirm" },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(desk.state.body).toBe(confirmed);
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
