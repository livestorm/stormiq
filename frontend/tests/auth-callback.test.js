import { describe, expect, it } from "vitest";
import { afterCallback, safeNextPath } from "../src/auth/callback";

describe("after the OAuth return page", () => {
  it("opens Sessions after a sign-in with nowhere to go back to", () => {
    expect(afterCallback("?auth=success")).toBe("/single-analysis");
    expect(afterCallback("?auth=success&next=%2F")).toBe("/single-analysis");
  });

  it("goes back to the page the user connected from", () => {
    expect(afterCallback("?auth=success&next=%2Fsingle-analysis%2Fabc%2Freport")).toBe("/single-analysis/abc/report");
    expect(afterCallback("?auth=success&next=/search")).toBe("/search");
  });

  it("opens the home page (the landing page) after a failed sign-in", () => {
    expect(afterCallback("?auth=error")).toBe("/");
    expect(afterCallback("")).toBe("/");
    expect(afterCallback("?auth=error&next=/search")).toBe("/");
  });

  it("never leaves the site", () => {
    for (const next of ["https://evil.example", "//evil.example", "/\\evil.example", "javascript:alert(1)", "evil", "/a\nb"]) {
      expect(safeNextPath(next)).toBe("");
      expect(afterCallback(`?auth=success&next=${encodeURIComponent(next)}`)).toBe("/single-analysis");
    }
  });

  it("never goes back to the OAuth pages or the API", () => {
    expect(safeNextPath("/auth/callback")).toBe("");
    expect(safeNextPath("/api/auth/livestorm/start")).toBe("");
    expect(safeNextPath("/" + "a".repeat(600))).toBe("");
  });
});
