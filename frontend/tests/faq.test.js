import { describe, expect, it, vi } from "vitest";
import {
  entryMarkdown,
  entryProblems,
  isDirty,
  mergeUpdates,
  moveEntry,
  newEntry,
  pendingUpdates,
  removeEntry,
  replayTime,
  sourceChip,
  toMarkdown,
  updatesLine,
  withAnswer,
} from "../src/followup/faq";
import { createFaq } from "../src/followup/useFaq";

const entry = (fields) => ({ id: "e", question: "Q?", answer: "", source: "none", t: null, origin: "desk", deskIds: [], ...fields });

const ENTRIES = [
  entry({ id: "d-1", question: "Will the slides be shared?", answer: "Yes.", source: "written", deskIds: ["qa:1"] }),
  entry({ id: "d-2", question: "Pricing?", answer: "Per team.", source: "talk", t: 724, deskIds: ["qa:2"] }),
  entry({ id: "d-3", question: "Is there an API?", deskIds: ["qa:3"] }),
];

function body(fields = {}) {
  return { sessionId: "s1", saved: true, updatedAt: "2026-11-11T10:00:00Z", entries: ENTRIES, deskUpdates: [], earlier: null, ...fields };
}

describe("the FAQ's words", () => {
  it("names each source, with the time in the replay for the talk and a live answer", () => {
    expect(sourceChip(ENTRIES[0]).label).toBe("Written answer");
    expect(sourceChip(entry({ answer: "x", source: "host" })).label).toBe("Host's reply");
    expect(sourceChip(ENTRIES[1]).label).toBe("From the talk · 12:04");
    expect(sourceChip(entry({ answer: "Live.", source: "aloud", t: 3725 })).label).toBe("Answered live · 1:02:05");
    expect(sourceChip(entry({ answer: "x", source: "manual" })).label).toBe("Added by you");
    expect(sourceChip(ENTRIES[2]).label).toBe("Not answered yet");
    expect(sourceChip(entry({ answer: "  ", source: "written" })).label).toBe("Not answered yet", "an empty answer is open");
  });

  it("formats replay times", () => {
    expect([null, -3, 0, 59.9, 61, 3600, "x"].map(replayTime)).toEqual(["", "", "0:00", "0:59", "1:01", "1:00:00", ""]);
  });

  it("writes the same Markdown as the server's download", () => {
    expect(toMarkdown(ENTRIES, "Launch")).toBe(
      "# FAQ: Launch\n\n## Will the slides be shared?\n\nYes.\n\n## Pricing?\n\nPer team.\n(12:04 in the replay)\n\n## Is there an API?\n\nNot answered yet.\n",
    );
    expect(toMarkdown([], "")).toBe("# FAQ\n");
    expect(entryMarkdown(ENTRIES[0])).toBe("## Will the slides be shared?\n\nYes.");
  });

  it("counts the desk updates", () => {
    expect(updatesLine([entry({ answer: "a" }), entry({ answer: "b" })])).toBe("2 questions newly answered on the desk");
    expect(updatesLine([entry({ answer: "a" }), entry({})])).toBe("1 question newly answered on the desk · 1 new question on the desk");
    expect(updatesLine([])).toBe("");
  });
});

describe("the FAQ's edits", () => {
  it("lets the source follow the answer", () => {
    expect(withAnswer(ENTRIES[2], "Typed")).toMatchObject({ answer: "Typed", source: "manual", t: null });
    expect(withAnswer(ENTRIES[1], "")).toMatchObject({ source: "none", t: null });
    expect(withAnswer(ENTRIES[1], "Edited")).toMatchObject({ source: "talk", t: 724 }, "an edited passage keeps its time");
  });

  it("adds, moves and removes entries", () => {
    const added = newEntry([...ENTRIES, entry({ id: "m-4" })]);
    expect(added).toEqual({ id: "m-5", question: "", answer: "", source: "none", t: null, origin: "manual", deskIds: [] });
    expect(moveEntry(ENTRIES, 2, -1).map((e) => e.id)).toEqual(["d-1", "d-3", "d-2"]);
    expect(moveEntry(ENTRIES, 0, -1).map((e) => e.id)).toEqual(["d-1", "d-2", "d-3"]);
    expect(removeEntry(ENTRIES, 1).map((e) => e.id)).toEqual(["d-1", "d-3"]);
  });

  it("checks the limits before a save", () => {
    expect(entryProblems(ENTRIES)).toEqual([]);
    expect(entryProblems([entry({ question: " " }), entry({ question: "x".repeat(501), answer: "y".repeat(4001) })]).map((p) => p.message)).toEqual([
      "Question 1 is empty.",
      "Question 2 is longer than 500 characters.",
      "Answer 2 is longer than 4000 characters.",
    ]);
    expect(entryProblems(Array.from({ length: 201 }, (_, i) => entry({ id: `m-${i}` })))[0].message).toBe("A FAQ holds at most 200 questions.");
  });

  it("is dirty only when something changed", () => {
    const copy = ENTRIES.map((e) => ({ ...e }));
    expect(isDirty(ENTRIES, copy)).toBe(false);
    copy[0].question = "Will the slides be shared? ";
    expect(isDirty(ENTRIES, copy)).toBe(false, "spaces around the text don't count");
    copy[0].answer = "No.";
    expect(isDirty(ENTRIES, copy)).toBe(true);
    expect(isDirty(ENTRIES, moveEntry(ENTRIES, 0, 1))).toBe(true);
  });

  it("merges desk updates into open entries and appends new questions, never over an answer", () => {
    const updates = [
      { ...entry({ id: "d-3", question: "Is there an API?", answer: "Yes, REST.", source: "host", deskIds: ["qa:3"] }), kind: "answered" },
      { ...entry({ id: "d-1", question: "Slides?", answer: "Desk text.", source: "host", deskIds: ["qa:1"] }), kind: "answered" },
      { ...entry({ id: "d-9", question: "New one?", deskIds: ["qa:9"] }), kind: "new" },
    ];
    const edited = ENTRIES.map((e) => (e.id === "d-3" ? { ...e, question: "API: is there one?" } : e));
    expect(pendingUpdates(edited, updates).map((u) => u.id)).toEqual(["d-3", "d-9"]);
    const merged = mergeUpdates(edited, updates);
    expect(merged[0].answer).toBe("Yes.", "an entry with an answer is never changed");
    expect(merged[2]).toMatchObject({ question: "API: is there one?", answer: "Yes, REST.", source: "host" });
    expect(merged[3]).toEqual({ id: "d-9", question: "New one?", answer: "", source: "none", t: null, origin: "desk", deskIds: ["qa:9"] });
    expect(pendingUpdates(merged, updates)).toEqual([]);
  });
});

describe("createFaq", () => {
  it("loads, edits, saves and discards", async () => {
    const saved = body({ entries: [{ ...ENTRIES[0], answer: "Edited." }, ENTRIES[1], ENTRIES[2]] });
    const api = { getSessionFaq: vi.fn(async () => body({ saved: false })), saveSessionFaq: vi.fn(async () => saved) };
    const faq = createFaq({ api });
    faq.open("s1");
    await vi.waitFor(() => expect(faq.state.body).not.toBeNull());
    expect(api.getSessionFaq).toHaveBeenCalledWith("s1", { signal: expect.any(AbortSignal) });
    expect(faq.dirty.value).toBe(false);
    faq.setAnswer(0, "Edited.");
    expect(faq.dirty.value).toBe(true);
    faq.discard();
    expect(faq.state.entries[0].answer).toBe("Yes.");
    faq.setAnswer(0, "Edited.");
    expect(await faq.save()).toBe(true);
    expect(api.saveSessionFaq.mock.calls[0][1].entries[0].answer).toBe("Edited.");
    expect(faq.state.body).toStrictEqual(saved);
    expect(faq.dirty.value).toBe(false);
  });

  it("refuses to send an entry without a question and keeps a failed save's edits", async () => {
    const api = { getSessionFaq: vi.fn(async () => body()), saveSessionFaq: vi.fn(async () => Promise.reject(new Error("Nope."))) };
    const faq = createFaq({ api });
    faq.open("s1");
    await vi.waitFor(() => expect(faq.state.body).not.toBeNull());
    faq.add();
    expect(await faq.save()).toBe(false);
    expect(faq.state.saveError).toBe("Question 4 is empty.");
    expect(api.saveSessionFaq).not.toHaveBeenCalled();
    faq.state.entries[3].question = "Added?";
    expect(await faq.save()).toBe(false);
    expect(faq.state.saveError).toBe("Nope.");
    expect(faq.state.entries).toHaveLength(4);
    expect(faq.dirty.value).toBe(true);
  });

  it("adds the desk updates on a click and moves and removes entries", async () => {
    const update = { ...entry({ id: "d-3", answer: "Yes.", source: "host", deskIds: ["qa:3"] }), kind: "answered" };
    const api = { getSessionFaq: vi.fn(async () => body({ deskUpdates: [update] })), saveSessionFaq: vi.fn() };
    const faq = createFaq({ api });
    faq.open("s1");
    await vi.waitFor(() => expect(faq.state.body).not.toBeNull());
    expect(faq.pending.value).toHaveLength(1);
    expect(faq.state.entries[2].answer).toBe("", "nothing changes before the click");
    faq.addUpdates();
    expect(faq.state.entries[2]).toMatchObject({ answer: "Yes.", source: "host" });
    expect(faq.pending.value).toEqual([]);
    faq.move(2, -1);
    faq.remove(0);
    expect(faq.state.entries.map((e) => e.id)).toEqual(["d-3", "d-2"]);
  });

  it("drops a late answer and a late save after another session opens", async () => {
    let resolve;
    const api = {
      getSessionFaq: vi.fn((id) => (id === "s1" ? new Promise((done) => (resolve = done)) : Promise.resolve(body({ sessionId: "s2" })))),
      saveSessionFaq: vi.fn(),
    };
    const faq = createFaq({ api });
    faq.open("s1");
    const signal = api.getSessionFaq.mock.calls[0][1].signal;
    faq.open("s2");
    expect(signal.aborted).toBe(true);
    await vi.waitFor(() => expect(faq.state.body?.sessionId).toBe("s2"));
    resolve(body({ sessionId: "s1" }));
    await Promise.resolve();
    expect(faq.state.body.sessionId).toBe("s2");

    let finish;
    api.saveSessionFaq = vi.fn(() => new Promise((done) => (finish = done)));
    faq.setAnswer(2, "Typed.");
    const pending = faq.save();
    faq.close();
    finish(body({ sessionId: "s2", entries: [] }));
    expect(await pending).toBe(false);
    expect(faq.state.entries).toHaveLength(3);
  });

  it("keeps unsaved edits when it reloads", async () => {
    const api = { getSessionFaq: vi.fn(async () => body()), saveSessionFaq: vi.fn() };
    const faq = createFaq({ api });
    faq.open("s1");
    await vi.waitFor(() => expect(faq.state.body).not.toBeNull());
    faq.setAnswer(2, "Mine.");
    faq.reload();
    await vi.waitFor(() => expect(api.getSessionFaq).toHaveBeenCalledTimes(2));
    await Promise.resolve();
    expect(faq.state.entries[2].answer).toBe("Mine.");
    expect(faq.dirty.value).toBe(true);
  });
});
