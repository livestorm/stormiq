import { describe, expect, it } from "vitest";
import body from "./fixtures/transcript-v1.json";
import { transcriptLines } from "../src/transcript/lines";
import { gifParts, gifUrl } from "../src/utils/gif";

const GIPHY = "https://media2.giphy.com/media/v1.Y2lk/rJsjyzDu6jkzibn8ly/giphy-downsized.gif";

describe("GIFs in the chat", () => {
  it("are GIPHY's and Tenor's media files, linked alone over https", () => {
    expect(gifUrl(GIPHY)).toBe(GIPHY);
    expect(gifUrl(`  ${GIPHY}\n`)).toBe(GIPHY);
    expect(gifUrl("https://i.giphy.com/abc.webp")).toBe("https://i.giphy.com/abc.webp");
    expect(gifUrl("https://media.tenor.com/x/tenor.gif")).toBe("https://media.tenor.com/x/tenor.gif");
    expect(gifUrl("https://c.tenor.com/x/tenor.gif")).toBe("https://c.tenor.com/x/tenor.gif");
  });

  it("leave everything else as text", () => {
    for (const text of [
      `look ${GIPHY}`,
      GIPHY.replace("https:", "http:"),
      "https://giphy.com/gifs/cat-abc",
      "https://media2.giphy.com/media/x/clip.mp4",
      "https://media2.giphy.com.evil.example/x.gif",
      "https://evil.example/media2.giphy.com/x.gif",
      "https://user:pw@media2.giphy.com/x.gif",
      "https://media2.giphy.com:8443/x.gif",
      "javascript:alert(1)//x.gif",
      "",
      null,
    ]) {
      expect(gifUrl(text), String(text)).toBe("");
    }
  });

  it("show under the message's other words when it has some", () => {
    expect(gifParts(`this is Dougs to me ${GIPHY}`)).toEqual({ text: "this is Dougs to me", gif: GIPHY });
    expect(gifParts(`when looking for a GIF\ndont ask ${GIPHY} lol`)).toEqual({ text: "when looking for a GIF dont ask lol", gif: GIPHY });
    expect(gifParts(GIPHY)).toEqual({ text: "", gif: GIPHY });
    expect(gifParts("see https://example.test/x.gif")).toEqual({ text: "see https://example.test/x.gif", gif: "" });
    expect(gifParts(null)).toEqual({ text: "", gif: "" });
  });

  it("are marked on the Transcript's chat lines only", () => {
    const room = [
      { id: "C9001", kind: "chat", t: 10, text: GIPHY, author: "Sam R.", team: false },
      { id: "Q9001", kind: "question", t: 11, text: GIPHY, author: "Sam R.", team: false },
    ];
    const shown = transcriptLines({ ...body, room, chapters: { state: "none", items: [] } }, { view: "room" });
    const byId = Object.fromEntries([...shown.lines, ...shown.room].map((line) => [line.id, line]));
    expect(byId.C9001.gif).toBe(GIPHY);
    expect(byId.Q9001.gif).toBe("");
    const withWords = transcriptLines(
      { ...body, room: [{ id: "C9002", kind: "chat", t: 12, text: `me today ${GIPHY}`, author: "Sam R.", team: false }], chapters: { state: "none", items: [] } },
      { view: "room" },
    );
    const line = [...withWords.lines, ...withWords.room][0];
    expect([line.gif, line.parts]).toEqual([GIPHY, [{ text: "me today", unclear: false }]]);
  });
});
