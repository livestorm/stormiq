import { afterEach, describe, expect, it, vi } from "vitest";
import { api, onUnauthorized } from "../src/api.js";
import { buildCsv, escapeCsvCell } from "../src/utils/csv.js";

function respond(status, body = {}) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

afterEach(() => {
  onUnauthorized(null);
  vi.unstubAllGlobals();
});

describe("api", () => {
  it("encodes session ids in every path", async () => {
    const fetch = vi.fn(() => respond(200, {}));
    vi.stubGlobal("fetch", fetch);
    await api.getCachedSession("a/../b?x=1");
    await api.getAiJobStatus("s 1", "deep-analysis", { language: "French" });
    expect(fetch.mock.calls[0][0]).toBe("/api/sessions/a%2F..%2Fb%3Fx%3D1/cached");
    expect(fetch.mock.calls[1][0]).toBe("/api/sessions/s%201/deep-analysis/job?language=French&outputsOnly=true");
    expect(api.getTranscriptJsonUrl("a/b")).toBe("/api/sessions/a%2Fb/transcript.json");
  });

  it("reports an expired session to the registered handler", async () => {
    vi.stubGlobal("fetch", vi.fn(() => respond(401, { detail: { message: "Please reconnect." } })));
    const handler = vi.fn();
    onUnauthorized(handler);
    await expect(api.getCachedSession("s1")).rejects.toMatchObject({ status: 401, message: "Please reconnect." });
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("does not treat other errors, or the auth endpoints, as an expired session", async () => {
    const handler = vi.fn();
    onUnauthorized(handler);
    vi.stubGlobal("fetch", vi.fn(() => respond(404)));
    await expect(api.getCachedSession("s1")).rejects.toMatchObject({ status: 404 });
    vi.stubGlobal("fetch", vi.fn(() => respond(401)));
    await expect(api.logout()).rejects.toMatchObject({ status: 401 });
    expect(handler).not.toHaveBeenCalled();
  });

  it("passes an abort signal through to fetch", async () => {
    const fetch = vi.fn(() => respond(200, {}));
    vi.stubGlobal("fetch", fetch);
    const controller = new AbortController();
    await api.getTranscriptJobStatus("s1", { signal: controller.signal });
    expect(fetch.mock.calls[0][1].signal).toBe(controller.signal);
    expect(fetch.mock.calls[0][1].headers["Content-Type"]).toBe("application/json");
  });
});

describe("CSV export", () => {
  it.each([
    ["=HYPERLINK(\"http://evil\",\"x\")", "'=HYPERLINK(\"\"http://evil\"\",\"\"x\"\")"],
    ["+1+cmd|' /C calc'!A0", "'+1+cmd|' /C calc'!A0"],
    ["-2+3", "'-2+3"],
    ["@SUM(A1)", "'@SUM(A1)"],
    ["\t=1", "'\t=1"],
  ])("neutralises a formula-like cell %j", (input, expected) => {
    const escaped = escapeCsvCell(input);
    expect(escaped.replace(/^"|"$/g, "")).toBe(expected);
  });

  it.each([[-5, "-5"], ["-5", "-5"], ["+3.2", "+3.2"], [0, "0"], [null, ""], ["plain text", "plain text"], ["a,b", "\"a,b\""]])(
    "leaves %j as data",
    (input, expected) => {
      expect(escapeCsvCell(input)).toBe(expected);
    },
  );

  it("builds rows with optional header labels", () => {
    const csv = buildCsv(["name", "note"], [{ name: "Ann", note: "=1+1" }], (column) => column.toUpperCase());
    expect(csv).toBe("NAME,NOTE\nAnn,'=1+1");
  });
});
