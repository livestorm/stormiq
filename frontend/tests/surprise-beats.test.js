import { describe, expect, it } from "vitest";
import { MAX_KEYWORDS, countUp, keywordsFrom, surpriseBeats } from "../src/followup/surpriseBeats";

const TAKE = `## Fifty messages and a working SSO

Amara clicked connect. The chat sent 50 messages in three minutes! Nobody believed it.

At 23:00 the room finally relaxed. 56% stayed for pricing.`;

describe("Surprise me, cut into beats", () => {
  it("opens on the title and ends each paragraph on its punchline", () => {
    const beats = surpriseBeats(TAKE, ["Single sign-on and SCIM", "Plans and pricing"]);
    expect(beats.map((beat) => beat.kind)).toEqual(["title", "line", "line", "punch", "line", "punch"]);
    expect(beats[3].text).toBe("Nobody believed it.");
  });

  it("marks the session's numbers and its key words, a few per beat", () => {
    const beats = surpriseBeats(TAKE, ["Plans and pricing"]);
    expect(beats[2].parts.filter((part) => part.kind === "number").map((part) => part.text)).toEqual(["50"]);
    expect(beats[4].parts.find((part) => part.kind === "number").text).toBe("23:00");
    expect(beats[5].parts.filter((part) => part.kind !== "plain").map((part) => [part.kind, part.text])).toEqual([
      ["number", "56%"],
      ["keyword", "pricing"],
    ]);
    expect(beats[0].parts.find((part) => part.kind === "keyword").text).toBe("SSO");
    expect(beats.every((beat) => beat.parts.filter((part) => part.kind === "keyword").length <= MAX_KEYWORDS)).toBe(true);
    expect(beats[5].parts.map((part) => part.text).join("")).toBe(beats[5].text);
  });

  it("keeps the meaningful words of chapter titles", () => {
    expect([...keywordsFrom(["Live demo: the analytics dashboard", "Q&A"])]).toEqual(["analytics", "dashboard"]);
  });

  it("counts a number up, never a clock time", () => {
    expect(countUp("1,200").format(1200)).toBe("1,200");
    expect(countUp("56%").format(28)).toBe("28%");
    expect(countUp("23:00")).toBe(null);
    expect(countUp("1")).toBe(null);
  });

  it("gives nothing for an empty take", () => {
    expect(surpriseBeats("", [])).toEqual([]);
  });
});
