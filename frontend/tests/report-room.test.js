// The v1 Report's room and questions sections wording (src/report/room.js,
// src/report/questions.js): the chat themes table, the question count and
// the Q&A's answers. The fixtures carry no chat labels, so the labelled
// measures are written here, shaped as `record.measures` sends them.

import { describe, expect, it } from "vitest";
import small from "./fixtures/record-v1.json";
import { chatThemes, labelStatus, languagesLabel, roomSummary, themeRowName } from "../src/report/room";
import { qaAnswers, questionCount, questionsSummary } from "../src/report/questions";

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

const qa = (rows = { written: 2, oral: 1, marked: 0, open: 1 }, state = "ok") => {
  const den = Object.values(rows).reduce((a, b) => a + b, 0);
  return {
    id: "m.questions_qa", version: 1, value: den ? (den - rows.open) / den : null, num: den - rows.open, den, state,
    coverage: { questions: den }, rows: Object.entries(rows).map(([key, questions]) => ({ key, questions })),
  };
};

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

describe("questionCount", () => {
  it("adds the chat's questions and counts the session's own apart", () => {
    const count = questionCount({ "m.questions_total": total(), "m.questions_qa": qa() });
    expect(count).toMatchObject({ value: "16", parts: ["4 in the Q&A", "12 in the chat"], status: "estimated" });
    expect(count.apart).toBe("3 chat questions about the session itself (a replay, the slides), counted apart.");
  });

  it("shows the Q&A alone, said as such, until the chat is labelled", () => {
    const count = questionCount(small.record.measures);
    expect(count).toMatchObject({ parts: ["in the Q&A"], status: "measured", apart: "" });
    expect(count.value).toBe(String(small.record.measures["m.questions_qa"].den));
    expect(count.note).toBe("Run the analysis to count the chat's questions.");
  });

  it("is null without questions or labels", () => {
    expect(questionCount({})).toBeNull();
  });
});

describe("qaAnswers", () => {
  it("words the four rows and the answered share", () => {
    const answers = qaAnswers({ "m.questions_qa": qa() });
    expect(answers.rows.map((row) => [row.label, row.questions, row.open])).toEqual([
      ["Answered in writing", "2", false], ["Answered aloud", "1", false], ["Marked answered", "0", false], ["Still open", "1", true],
    ]);
    expect(answers.rate).toBe("75%");
    expect(answers.status).toBe("measured");
  });

  it("gives the median delay only when its measure shows", () => {
    const shown = { state: "ok", value: 95 };
    expect(qaAnswers({ "m.questions_qa": qa(), "m.time_to_answer": shown }).delay).toBe("1 min 35 s");
    const gated = qaAnswers({ "m.questions_qa": qa(), "m.time_to_answer": { state: "insufficient", value: null } });
    expect(gated.delay).toBe("");
    expect(gated.note).toBe("Too few written answers with a time for a median delay.");
  });

  it("says when the Q&A was empty, and is null when it wasn't fetched", () => {
    expect(qaAnswers({ "m.questions_qa": qa({ written: 0, oral: 0, marked: 0, open: 0 }, "insufficient") })).toMatchObject({
      rows: [], note: "No question in the Q&A.",
    });
    expect(qaAnswers({ "m.questions_qa": { state: "unavailable", rows: [] } })).toBeNull();
  });
});

describe("questionsSummary and labelStatus", () => {
  it("sums up the count and the open Q&A questions", () => {
    expect(questionsSummary({ "m.questions_total": total(), "m.questions_qa": qa() })).toEqual({
      takeaway: "16 questions: 4 in the Q&A, 12 in the chat.", chip: "1 still open in Q&A",
    });
  });

  it("marks anything the labels count as estimated", () => {
    expect(labelStatus(total())).toBe("estimated");
    expect(labelStatus(total(undefined, "unavailable"))).toBe("");
  });
});
