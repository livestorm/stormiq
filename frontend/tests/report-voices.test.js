// The v1 Report's "Who spoke up" wording (src/report/voices.js), against
// `tests/fixtures/voices-v1.json`, which the backend generates from the
// real route, and the reader that loads it (src/report/useFindings.js).

import { describe, expect, it, vi } from "vitest";
import voices from "./fixtures/voices-v1.json";
import { voicesView } from "../src/report/voices";
import { createVoices } from "../src/report/useFindings";

describe("voicesView", () => {
  it("lists who wrote, the most active first, with what they wrote and one of their messages", () => {
    const view = voicesView(voices);
    expect(view.lead).toBe(
      `${voices.total.people} people in the audience wrote: ${voices.total.messages} messages · ${voices.total.questions} questions.`,
    );
    expect(view.rows).toHaveLength(voices.people.length);
    const first = view.rows[0];
    const person = voices.people[0];
    expect(first).toMatchObject({ name: person.name, named: true, share: `${Math.round(person.share * 100)}%`, quote: person.sample.text });
    expect(first.counts).toMatch(/^\d+ messages? · \d+ questions?$/);
    expect(view.others).toBe("");
    expect(view.unnamed).toBe("");
  });

  it("says “An attendee” for someone it may not name, and why", () => {
    const body = { ...voices, people: [{ ...voices.people[0], name: null, company: null }] };
    const view = voicesView(body);
    expect(view.rows[0]).toMatchObject({ name: "An attendee", named: false, company: "" });
    expect(view.unnamed).toMatch(/couldn't be matched to a registrant/);
  });

  it("sums up the people past the list, and words a message before the start", () => {
    const body = {
      ...voices,
      people: [{ ...voices.people[0], sample: { id: "Q001", t: -45, text: "Hi" } }],
      others: { people: 12, messages: 20, questions: 1 },
    };
    const view = voicesView(body);
    expect(view.others).toBe("And 12 more people: 20 messages · 1 question.");
    expect(view.rows[0].time).toBe("Before the start");
  });

  it("says when nobody wrote, and is null before it loads", () => {
    const empty = { ...voices, total: { people: 0, messages: 0, questions: 0 }, people: [] };
    expect(voicesView(empty)).toMatchObject({ rows: [], empty: "Nobody in the audience wrote in the chat or the Q&A." });
    expect(voicesView(null)).toBeNull();
  });
});

describe("createVoices", () => {
  it("loads the list with the scope's signal and drops a late answer for another session", async () => {
    let resolveFirst;
    const api = {
      getSessionVoices: vi.fn((id) => (id === "a" ? new Promise((resolve) => (resolveFirst = resolve)) : Promise.resolve(voices))),
    };
    const model = createVoices({ api });
    model.open("a");
    expect(api.getSessionVoices.mock.calls[0][1].signal).toBeInstanceOf(AbortSignal);
    model.open("b");
    await Promise.resolve();
    resolveFirst({ total: { people: 99 } });
    await Promise.resolve();
    expect(model.state.body).toEqual(voices);
    model.close();
  });

  it("keeps the error to show with Try again", async () => {
    const api = { getSessionVoices: vi.fn(() => Promise.reject(new Error("Nope"))) };
    const model = createVoices({ api });
    model.open("a");
    await new Promise((resolve) => setTimeout(resolve));
    expect(model.state.error).toBe("Nope");
  });
});
