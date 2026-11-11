import { describe, expect, it, vi } from "vitest";
import {
  OLD_LIST_NOTE,
  RECORDING_CLOCK_NOTE,
  clockText,
  decisionView,
  decisionsFileName,
  initials,
} from "../src/followup/decisions";
import { createDecisions } from "../src/followup/useDecisions";

// What `GET /decisions?language=` sends (`api_logic.get_session_decisions`):
// the speakers already named, from the names saved on the Transcript tab.
function body(names = { owner: "Nora Vale" }) {
  return {
    sessionId: "s1",
    language: "English",
    saved: "items",
    clock: "session",
    markdown: `## Decisions\n\n- **The launch moves to March.** ${names.owner} at 4:05: *“we move it”*`,
    items: [
      {
        kind: "commitment",
        text: "Send the slides to everyone",
        due: "by Friday",
        quote: "I'll send the slides by Friday",
        utterance: "U0004",
        at: 245,
        owner: { speaker: "S0", name: names.owner },
        saidBy: { speaker: "S0", name: names.owner },
      },
      {
        kind: "decision",
        text: "The launch moves to March.",
        due: null,
        quote: "we move it",
        utterance: "U0005",
        at: 3729,
        owner: null,
        saidBy: { speaker: "S1", name: "Speaker 2" },
      },
      {
        kind: "commitment",
        text: "Look into the export bug",
        due: null,
        quote: "the product team will look into it",
        utterance: "U0009",
        at: null,
        owner: { speaker: null, name: "the product team" },
        saidBy: null,
      },
    ],
  };
}

describe("the decisions' cards", () => {
  it("list decisions first, then commitments, each kind with its icon and word", () => {
    const view = decisionView(body());
    expect(view.saved).toBe("items");
    expect(view.sections.map((s) => [s.title, s.word, s.icon, s.count])).toEqual([
      ["Decisions", "Decision", "check", 1],
      ["Commitments", "Commitment", "user-check", 2],
    ]);
    expect(view.note).toBe("");
    expect(view.empty).toBe(false);
  });

  it("word each card: the text, the owner with initials, the due date, the time and who said it", () => {
    const [decisions, commitments] = decisionView(body()).sections;
    const [slides, bug] = commitments.items;
    expect(slides.text).toBe("Send the slides to everyone.");
    expect(slides.owner).toEqual({ name: "Nora Vale", initials: "NV", speaker: true, label: "Owner: Nora Vale" });
    expect(slides.due).toEqual({ text: "by Friday", label: "Due: by Friday" });
    expect(slides.at).toEqual({ text: "4:05", label: "Said at 4:05 on the session clock" });
    expect(slides.attribution).toBe("Nora Vale at 4:05");
    expect(decisions.items[0].text).toBe("The launch moves to March.");
    expect(decisions.items[0].owner).toBe(null);
    expect(decisions.items[0].at.text).toBe("1:02:09");
    expect(bug.owner).toMatchObject({ name: "the product team", initials: "TT", speaker: false });
    expect(bug.at).toBe(null);
    expect(bug.attribution).toBe("A speaker");
  });

  it("show the name the server sends now, so a rename on the Transcript tab shows at once", () => {
    const renamed = decisionView(body({ owner: "Nora Lindqvist" })).sections[1].items[0];
    expect(renamed.owner.name).toBe("Nora Lindqvist");
    expect(renamed.owner.initials).toBe("NL");
    expect(renamed.attribution).toBe("Nora Lindqvist at 4:05");
  });

  it("say when times are the recording's", () => {
    const view = decisionView({ ...body(), clock: "recording" });
    expect(view.note).toBe(RECORDING_CLOCK_NOTE);
    expect(view.sections[1].items[0].at.label).toBe("Said at 4:05 in the recording");
  });

  it("title the sections in French and say when nothing was stated", () => {
    expect(decisionView(body(), "French").sections.map((s) => s.title)).toEqual(["Décisions", "Engagements"]);
    const none = decisionView({ ...body(), items: [] });
    expect(none.empty).toBe(true);
    expect(none.emptyText).toBe("No decisions or commitments were stated on stage.");
    expect(none.note).toBe("");
    expect(decisionView({ ...body(), items: [] }, "French").emptyText).toMatch(/^Aucune décision/);
  });

  it("show a list saved before names could update as its Markdown, with a note", () => {
    const view = decisionView({ saved: "markdown", markdown: "## Commitments\n\n- **Send the slides.**", items: [], clock: null });
    expect(view.saved).toBe("markdown");
    expect(view.note).toBe(OLD_LIST_NOTE);
    expect(view.sections).toEqual([]);
    expect(view.markdown).toBe("## Commitments\n\n- **Send the slides.**");
    expect(decisionView(null).saved).toBe("none");
  });

  it("keep Copy and the download on the server's Markdown", () => {
    expect(decisionView(body()).markdown).toContain("Nora Vale at 4:05");
    expect(decisionsFileName("abc/1", "French")).toBe("stormiq-decisions-abc-1-fr.md");
    expect(decisionsFileName("abc", "English")).toBe("stormiq-decisions-abc.md");
  });

  it("format times and initials", () => {
    expect([clockText(65), clockText(3729), clockText(null), clockText("x")]).toEqual(["1:05", "1:02:09", "", ""]);
    expect([initials("Nora"), initials("Speaker 2"), initials("  "), initials("Élodie de la Tour")]).toEqual(["N", "S2", "?", "ÉT"]);
  });
});

function deferred() {
  let resolve;
  const promise = new Promise((done) => (resolve = done));
  return { promise, resolve };
}

describe("the decisions' data", () => {
  it("loads the list in the language shown, with the scope's signal", async () => {
    const answer = body();
    const api = { getSessionDecisions: vi.fn(async () => answer) };
    const decisions = createDecisions({ api });
    decisions.open("s1", "French", "v1");
    await vi.waitFor(() => expect(decisions.state.body).toBe(answer));
    expect(api.getSessionDecisions).toHaveBeenCalledWith("s1", "French", expect.objectContaining({ signal: expect.any(AbortSignal) }));
  });

  it("loads again when a job saves a new list, and not for the same one", async () => {
    const api = { getSessionDecisions: vi.fn(async () => body()) };
    const decisions = createDecisions({ api });
    decisions.open("s1", "English", "v1");
    decisions.open("s1", "English", "v1");
    expect(api.getSessionDecisions).toHaveBeenCalledTimes(1);
    await vi.waitFor(() => expect(decisions.state.body).not.toBe(null));
    decisions.open("s1", "English", "v2");
    expect(decisions.state.body).not.toBe(null);
    expect(api.getSessionDecisions).toHaveBeenCalledTimes(2);
  });

  it("drops a late answer for a list it left, and aborts its request", async () => {
    const first = deferred();
    const french = { ...body(), language: "French" };
    const api = { getSessionDecisions: vi.fn((id, language) => (language === "English" ? first.promise : Promise.resolve(french))) };
    const decisions = createDecisions({ api });
    decisions.open("s1", "English");
    const signal = api.getSessionDecisions.mock.calls[0][2].signal;
    decisions.open("s1", "French");
    expect(signal.aborted).toBe(true);
    await vi.waitFor(() => expect(decisions.state.body).toBe(french));
    first.resolve(body());
    await Promise.resolve();
    expect(decisions.state.body).toBe(french);
  });

  it("keeps the error to show with Try again", async () => {
    const api = { getSessionDecisions: vi.fn(async () => Promise.reject(new Error("The decisions could not be read."))) };
    const decisions = createDecisions({ api });
    decisions.open("s1", "English");
    await vi.waitFor(() => expect(decisions.state.error).toBe("The decisions could not be read."));
    api.getSessionDecisions.mockResolvedValueOnce(body());
    decisions.reload();
    await vi.waitFor(() => expect(decisions.state.body).not.toBe(null));
    expect(decisions.state.error).toBe("");
  });
});
