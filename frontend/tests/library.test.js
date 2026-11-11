import { describe, expect, it } from "vitest";
import { libraryCells, peakLabel, sparkPaths } from "../src/sessions/library";
import { createLibraryRows } from "../src/sessions/useLibraryRows";

describe("a session's library cells", () => {
  it("draw the shape only when the room wrote", () => {
    const paths = sparkPaths([0, 4, 2], { width: 10, height: 10 });
    expect(paths.line).toBe("M0.0 9.0 L5.0 1.0 L10.0 5.0");
    expect(paths.area.endsWith("L10 10 L0 10 Z")).toBe(true);
    expect(sparkPaths([0, 0, 0])).toBe(null);
    expect(sparkPaths([3])).toBe(null);
  });

  it("name the busiest minute", () => {
    expect(peakLabel({ minute: 23, messages: 26 })).toBe("Audience chat per minute, busiest at 23:00, 26 messages");
    expect(peakLabel(null)).toBe("No audience chat");
  });

  it("word the numbers as the Report shows them and the lead finding with its templates", () => {
    const cells = libraryCells({
      sessionId: "s1",
      sparkline: [1, 2],
      peak: { minute: 1, messages: 2 },
      wrote: { value: 0.7342, estimated: true },
      questions: null,
      stillOpen: 38,
      lead: { id: "moments", values: { moments: [{ start: 1380, end: 1560, messages: 50, authors: 33, ratio: 13 }] } },
      analysed: true,
    });
    expect([cells.wrote, cells.wroteEstimated, cells.questions, cells.open]).toEqual(["73%", true, "—", "38 open"]);
    expect(cells.lead).toMatch(/23:00/);
    expect(cells.sparkline).toEqual([1, 2]);
    expect(libraryCells(null)).toBe(null);
    expect(libraryCells({ stillOpen: 0, lead: { id: "not-a-finding" } }).lead).toBe("");
  });
});

function fakeApi() {
  const calls = [];
  return {
    calls,
    getSessionLibraryRow(id, { signal }) {
      return new Promise((resolve, reject) => calls.push({ id, signal, resolve, reject }));
    },
  };
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("the library's rows", () => {
  it("ask a few rows at a time, each once", async () => {
    const api = fakeApi();
    const rows = createLibraryRows({ api, concurrency: 2 });
    ["a", "b", "c", "a"].forEach(rows.want);
    expect(api.calls.map((call) => call.id)).toEqual(["a", "b"]);
    expect(rows.state.rows.c.loading).toBe(true);
    api.calls[0].resolve({ sparkline: [1] });
    await flush();
    expect(rows.state.rows.a.body.sparkline).toEqual([1]);
    expect(api.calls.map((call) => call.id)).toEqual(["a", "b", "c"]);
    api.calls[1].reject(new Error("This session's summary could not be read."));
    await flush();
    expect(rows.state.rows.b.error).toBe("This session's summary could not be read.");
  });

  it("drop everything on close, late answers included", async () => {
    const api = fakeApi();
    const rows = createLibraryRows({ api, concurrency: 1 });
    rows.want("a");
    rows.want("b");
    rows.close();
    expect(api.calls[0].signal.aborted).toBe(true);
    api.calls[0].resolve({ sparkline: [9] });
    await flush();
    expect(rows.state.rows.a.body).toBe(null);
    // The queued row is never asked.
    expect(api.calls).toHaveLength(1);
  });
});
