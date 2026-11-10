import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// The "StormIQ for Livestorm" lockup (components/ui/ForLivestorm.vue) shows one
// of two official logo files, chosen by tokens. These checks keep the swap and
// the files in shape (docs/brand.md).
const tokens = readFileSync(new URL("../src/design/tokens.css", import.meta.url), "utf8");

function block(selectorStart) {
  const start = tokens.indexOf(selectorStart);
  expect(start, `${selectorStart} not found in tokens.css`).toBeGreaterThan(-1);
  return tokens.slice(start, tokens.indexOf("}", start));
}

describe("Livestorm logo tokens", () => {
  it("shows the on-light logo by default", () => {
    const root = block(":root {");
    expect(root).toContain("--siq-logo-on-light: block;");
    expect(root).toContain("--siq-logo-on-dark: none;");
  });

  it("shows the on-dark logo in both dark blocks", () => {
    for (const selector of [':root[data-theme="dark"] {', ':root:not([data-theme="light"]) {']) {
      const css = block(selector);
      expect(css).toContain("--siq-logo-on-light: none;");
      expect(css).toContain("--siq-logo-on-dark: block;");
    }
  });
});

describe("Livestorm logo files", () => {
  for (const variant of ["on-light", "on-dark"]) {
    it(`logo-${variant}.svg scales from its viewBox`, () => {
      const svg = readFileSync(new URL(`../src/assets/livestorm/logo-${variant}.svg`, import.meta.url), "utf8");
      expect(svg).toMatch(/<svg [^>]*viewBox="0 0 922 155"/);
    });
  }
});
