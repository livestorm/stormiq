import { describe, expect, it } from "vitest";
import { renamedSessionsPath } from "../src/sessions/paths";

describe("links from before the /sessions rename", () => {
  it("open the same page under /sessions", () => {
    expect(renamedSessionsPath("/single-analysis")).toBe("/sessions");
    expect(renamedSessionsPath("/single-analysis/abc")).toBe("/sessions/abc");
    expect(renamedSessionsPath("/single-analysis/abc/report")).toBe("/sessions/abc/report");
    expect(renamedSessionsPath("/single-analysis/abc/transcript?t=90#U0012")).toBe("/sessions/abc/transcript?t=90#U0012");
    expect(renamedSessionsPath("/single-analysis?ui=v1")).toBe("/sessions?ui=v1");
  });

  it("leave every other path alone", () => {
    expect(renamedSessionsPath("/sessions/abc/report")).toBe("/sessions/abc/report");
    expect(renamedSessionsPath("/single-analysisx")).toBe("/single-analysisx");
    expect(renamedSessionsPath("/search")).toBe("/search");
  });
});
