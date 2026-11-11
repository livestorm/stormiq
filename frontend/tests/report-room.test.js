// The v1 Report's room and questions sections wording (src/report/room.js,
// src/report/questions.js): the chat themes table, and every question as
// the desk lists it (still open, answered, with its answers). The record
// fixtures carry no chat labels, so the labelled measures are written here,
// shaped as `record.measures` sends them; the questions come from the desk's
// fixture, generated from the real route.

import { describe, expect, it } from "vitest";
import small from "./fixtures/record-v1.json";
import { chatThemes, labelStatus, languagesLabel, roomSummary, themeRowName } from "../src/report/room";
import desk from "./fixtures/questions-v1.json";
import { answerDelay, questionGroups, questionItem, questionNotes, questionsHeadline } from "../src/report/questions";

const theme = (key, messages, share, ids = [], languages = [{ language: "en", messages }]) => ({
  key, messages, share, languages, ids,
});

function themes({ state = "ok", den = 60, rows } = {}) {
  return {
    id: "m.chat_themes", version: 1, value: null, num: null, den, state,
    coverage: { chatMessages: den, teamMessages: 3, labels: "model" }, excluded: { team: 2, guestSpeakers: 0, total: 2, internal: false },
    livestorm: null,
    rows: rows ?? [
      theme("content", 30, 0.5, ["C0001", "C0002"], [{ language: "en", messages: 28 }, { language: "fr", messages: 2 }]),
      theme("question", 12, 0.2, ["C0003"]),
      theme("tech", 0, 0),
      theme("logistics", 6, 0.1),
      theme("social", 12, 0.2),
      theme("laughter", 0, 0),
      theme("off_topic", 0, 0),
    ],
  };
}

const total = (rows = { qa: 4, chat: 12, chatLogistics: 3 }, state = "ok") => ({
  id: "m.questions_total", version: 1, value: rows.qa + rows.chat, state, den: null, num: null,
  coverage: { chatMessages: 60, teamMessages: 3, labels: "model" },
  rows: Object.entries(rows).map(([key, questions]) => ({ key, questions })),
});

describe("chatThemes", () => {
  it("lists the themes with messages in the server's order, with shares and languages", () => {
    const table = chatThemes(themes());
    expect(table.rows.map((row) => row.key)).toEqual(["content", "question", "logistics", "social"]);
    expect(table.rows[0]).toMatchObject({
      label: "Reactions to the content", messages: "30", share: "50%", bar: 50, languages: "English, French (2)", ids: ["C0001", "C0002"],
    });
    expect(table.notes).toEqual(["No message about: sound, video and connection, laughter, off topic."]);
    expect(table.total).toBe("60 audience messages");
    expect(table.status).toBe("estimated");
    expect(table.info).toContain("3 messages from your team left out.");
  });

  it("keeps the counts and hides the shares below the gate", () => {
    const rows = [theme("content", 5, null, ["C0001"]), theme("social", 3, null)];
    const table = chatThemes(themes({ state: "insufficient", den: 8, rows }));
    expect(table.rows.map((row) => [row.messages, row.share, row.bar])).toEqual([["5", "—", null], ["3", "—", null]]);
    expect(table.notes[0]).toBe("Fewer than 30 audience messages: the counts are shown, not the shares.");
    expect(table.status).toBe("");
  });

  it("asks for the analysis while the chat isn't labelled", () => {
    const table = chatThemes(small.record.measures["m.chat_themes"]);
    expect(table).toMatchObject({ rows: [], empty: "Run the analysis to sort the chat by theme.", status: "" });
    expect(chatThemes(undefined)).toBeNull();
  });

  it("says when the audience wrote nothing", () => {
    const rows = ["content", "question"].map((key) => theme(key, 0, null));
    expect(chatThemes(themes({ state: "insufficient", den: 0, rows }))).toMatchObject({
      rows: [], empty: "The audience wrote nothing in the chat.", notes: [], total: "",
    });
  });

  it("names each row in one sentence for screen readers", () => {
    const [content] = chatThemes(themes()).rows;
    expect(themeRowName(content)).toBe("Reactions to the content: 30 messages, 50% of the chat, English, French (2)");
    const gated = chatThemes(themes({ state: "insufficient", den: 8, rows: [theme("social", 1, null, [], [])] })).rows[0];
    expect(themeRowName(gated)).toBe("Greetings and thanks: 1 message");
  });

  it("names the languages, and a message without words isn't one", () => {
    expect(languagesLabel([{ language: null, messages: 4 }])).toBe("—");
    expect(languagesLabel([{ language: null, messages: 4 }, { language: "fr", messages: 1 }])).toBe("French");
  });
});

describe("roomSummary", () => {
  it("names the largest theme when shares show, the first on a tie", () => {
    expect(roomSummary({ "m.chat_themes": themes() })).toEqual({
      takeaway: "Most of the chat: reactions to the content (50%).", chip: "60 audience messages",
    });
    const tied = themes({ rows: [theme("question", 20, 0.5), theme("social", 20, 0.5)], den: 40 });
    expect(roomSummary({ "m.chat_themes": tied }).takeaway).toBe("Most of the chat: questions (50%).");
  });

  it("stays general without shares", () => {
    const gated = themes({ state: "insufficient", den: 8, rows: [theme("content", 8, null)] });
    expect(roomSummary({ "m.chat_themes": gated })).toEqual({ takeaway: "What the chat was about.", chip: "8 audience messages" });
    expect(roomSummary({})).toEqual({ takeaway: "What the chat was about.", chip: "" });
  });
});

describe("questionGroups", () => {
  it("lists every question, still open first, then answered, with the desk's counts", () => {
    const groups = questionGroups(desk);
    expect(groups.map((group) => [group.key, group.title, group.count])).toEqual([
      ["open", "Still open", 2],
      ["answered", "Answered", 3],
    ]);
    const ids = groups.flatMap((group) => group.items.map((item) => item.id));
    expect(ids.sort()).toEqual(desk.questions.map((q) => q.id).sort());
    expect(groups[0].items.every((item) => ["Still open", "Covered in the talk, not marked"].includes(item.status.label))).toBe(true);
  });

  it("drops an empty group, and has none without a desk", () => {
    const answeredOnly = { ...desk, summary: { ...desk.summary, open: 0, covered: 0 }, questions: desk.questions.filter((q) => ["written", "aloud", "here"].includes(q.status)) };
    expect(questionGroups(answeredOnly).map((group) => group.key)).toEqual(["answered"]);
    expect(questionGroups(null)).toEqual([]);
  });
});

describe("questionItem", () => {
  it("says who asked, where and when", () => {
    const item = questionItem(desk.questions.find((q) => q.id === "Q001"));
    expect(item).toMatchObject({ text: "Will the slides be shared?", source: "qa", who: "Theo P. · Q&A tab · Before" });
    expect(item.answers).toEqual([{ label: "Written answer", text: "Yes, see the chat.", quote: false }]);
  });

  it("shows the passage of the talk that answers it, and the host's own answer first", () => {
    const covered = questionItem(desk.questions.find((q) => q.id === "C0010"));
    expect(covered.answers).toEqual([
      { label: "Seems answered in the talk at 5:19", text: "Pricing works per team and scales with seats.", quote: true },
    ]);
    const replied = questionItem({ id: "Q9", text: "?", status: "here", reply: "Mine", answer: "Theirs" });
    expect(replied.answers.map((answer) => answer.label)).toEqual(["Your answer", "Written answer"]);
  });
});

describe("questionsHeadline, answerDelay and questionNotes", () => {
  it("sums up the desk: how many, answered and still open", () => {
    expect(questionsHeadline(desk)).toEqual({ takeaway: "5 questions: 3 answered, 2 still open.", chip: "2 still open" });
    expect(questionsHeadline({ summary: { total: 0 } })).toEqual({ takeaway: "No audience question in the Q&A or the chat.", chip: "" });
  });

  it("falls back on the record's count before the desk loads", () => {
    expect(questionsHeadline(null, { "m.questions_total": total() }).takeaway).toBe("16 questions.");
    expect(questionsHeadline(null, {}).takeaway).toBe("What the audience asked, and what was answered.");
  });

  it("gives the median delay only when its measure shows", () => {
    expect(answerDelay({ "m.time_to_answer": { state: "ok", value: 95 } })).toBe("Written answers came 1 min 35 s after the question (median).");
    expect(answerDelay({ "m.time_to_answer": { state: "insufficient", value: null } })).toBe("");
  });

  it("notes the requests counted apart and why chat questions may be missing", () => {
    expect(questionNotes(desk)).toEqual(["1 replay or slides request in the chat, counted apart."]);
    expect(questionNotes({ summary: { chatQuestions: "none" } })[0]).toMatch(/once the session is analysed/);
  });
});

describe("labelStatus", () => {
  it("marks anything the labels count as estimated", () => {
    expect(labelStatus(total())).toBe("estimated");
    expect(labelStatus(total(undefined, "unavailable"))).toBe("");
  });
});
