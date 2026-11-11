import { describe, expect, it, vi } from "vitest";
import body from "./fixtures/figures-v1.json";
import { chosenLabel, figuresSummary, momentLine, uncheckedNote } from "../src/followup/figures";
import { createFigureChecks } from "../src/followup/useFigureChecks";

describe("Before you share's words", () => {
  it("count what is left to check, from the server's checks", () => {
    expect(figuresSummary(body)).toBe("2 figures said on stage to check before you share");
    expect(figuresSummary({ ...body, unchecked: 1 })).toBe("1 figure said on stage to check before you share");
    expect(figuresSummary({ ...body, unchecked: 0 })).toBe("All 2 figures checked");
    expect(figuresSummary({ checks: [] })).toBe("");
    expect(uncheckedNote(body)).toBe("2 figures said on stage aren't checked yet: see Before you share, above.");
    expect(uncheckedNote({ unchecked: 1 })).toMatch(/^1 figure said on stage isn't checked yet/);
    expect(uncheckedNote({ unchecked: 0 })).toBe("");
  });

  it("word each moment and the answer chosen", () => {
    const [values, acronym] = body.checks;
    expect(values.question).toBe("Seats per team: which value is right?");
    expect(momentLine(acronym.moments[0])).toBe("5:19 · “pricing works per team”");
    expect(momentLine({ t: null, quote: "x" })).toBe("“x”");
    expect(chosenLabel(acronym)).toBe("");
    expect(chosenLabel({ ...values, choice: "both" })).toBe("Both: they measure different things");
  });
});

describe("Before you share's data", () => {
  it("loads the checks and saves an answer with the scope's signal", async () => {
    const answered = { ...body, unchecked: 1 };
    const api = { getFigureChecks: vi.fn(async () => body), saveFigureCheck: vi.fn(async () => answered) };
    const figures = createFigureChecks({ api });
    figures.open("s1");
    await vi.waitFor(() => expect(figures.state.body).toBe(body));
    expect(await figures.answer(body.checks[1].key, "NPS")).toBe(true);
    expect(api.saveFigureCheck).toHaveBeenCalledWith(
      "s1",
      { key: body.checks[1].key, choice: "NPS" },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(figures.state.body).toBe(answered);
  });

  it("keeps the checks and says why when an answer fails, and drops a late one", async () => {
    const error = Object.assign(new Error("No such check."), { status: 409 });
    const api = { getFigureChecks: vi.fn(async () => body), saveFigureCheck: vi.fn(async () => Promise.reject(error)) };
    const figures = createFigureChecks({ api });
    figures.open("s1");
    await vi.waitFor(() => expect(figures.state.body).toBe(body));
    expect(await figures.answer("k", "NPS")).toBe(false);
    expect(figures.state.saveError).toEqual({ key: "k", message: "No such check." });
    let resolve;
    api.saveFigureCheck = vi.fn(() => new Promise((done) => (resolve = done)));
    const pending = figures.answer("k", "NPS");
    figures.open("s2");
    resolve({ ...body, sessionId: "late" });
    expect(await pending).toBe(false);
    expect(figures.state.body?.sessionId).not.toBe("late");
  });
});
