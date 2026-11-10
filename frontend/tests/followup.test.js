import { describe, expect, it } from "vitest";
import {
  LEGACY_OUTPUTS,
  OUTPUTS,
  canMake,
  cleanRecap,
  making,
  outputFor,
  outputGroups,
  outputText,
  pdfUrl,
} from "../src/followup/outputs";
import { inlineParts, markdownBlocks } from "../src/followup/markdown";

const outputs = {
  analysisBundle: { English: "## Summary\nIt went well." },
  deepAnalysisBundle: {},
  smartRecapBundle: { professional: "# Title\nSpring launch recap\n# Description\nThe session, in short." },
  contentRepurposeBundle: { English: { summary: "Short.", blog: "Long.", email: "Hi all,", social_media: "Post" } },
};

const fakeApi = {
  getAnalysisPdfUrl: (id, kind, language) => `/a/${id}/${kind}/${language}`,
  getSmartRecapPdfUrl: (id, tone) => `/r/${id}/${tone}`,
  getContentRepurposingPdfUrl: (id, language, item) => `/c/${id}/${language}/${item}`,
};

describe("the Follow-up outputs", () => {
  it("read each output from where the workspace keeps it", () => {
    expect(outputText(outputs, outputFor("overall"), "English")).toBe("## Summary\nIt went well.");
    expect(outputText(outputs, outputFor("overall"), "French")).toBe("");
    expect(outputText(outputs, outputFor("deep"), "English")).toBe("");
    expect(outputText(outputs, outputFor("email"), "English")).toBe("Hi all,");
    expect(outputText(outputs, outputFor("recap-professional"), "French")).toBe("## Spring launch recap\nThe session, in short.");
    expect(outputText(outputs, outputFor("recap-hype"), "English")).toBe("");
  });

  it("clean a recap's Title and Description labels as the classic view did", () => {
    expect(cleanRecap("## title\nA day\n## Description\nBody")).toBe("## A day\nBody");
    expect(cleanRecap("")).toBe("");
  });

  it("let French be written only once the English one exists", () => {
    expect(canMake(outputs, outputFor("overall"), "French")).toBe(true);
    expect(canMake(outputs, outputFor("deep"), "French")).toBe(false);
    expect(canMake(outputs, outputFor("deep"), "English")).toBe(true);
    expect(canMake({}, outputFor("recap-hype"), "French")).toBe(true);
  });

  it("say an output is being written only for its own slot", () => {
    const state = { loading: { analysis: true }, aiJobs: { overall_analysis: { language: "French", progress: { percent: 42.6 } } } };
    expect(making(state, outputFor("overall"), "French")).toEqual({ busy: true, progress: 43 });
    expect(making(state, outputFor("overall"), "English")).toEqual({ busy: false, progress: null });
    expect(making(state, outputFor("deep"), "French")).toEqual({ busy: false, progress: null });
    const recap = { loading: { smartRecap: true }, aiJobs: { smart_recap: { tone: "hype" } } };
    expect(making(recap, outputFor("recap-hype"), "English").busy).toBe(true);
    expect(making(recap, outputFor("recap-professional"), "English").busy).toBe(false);
  });

  it("point each output at its PDF", () => {
    expect(pdfUrl(fakeApi, "s1", outputFor("deep"), "French")).toBe("/a/s1/deep/French");
    expect(pdfUrl(fakeApi, "s1", outputFor("recap-surprise"), "English")).toBe("/r/s1/surprise");
    expect(pdfUrl(fakeApi, "s1", outputFor("social"), "English")).toBe("/c/s1/English/social_media");
    expect(pdfUrl(fakeApi, "", outputFor("deep"), "English")).toBe("");
  });

  it("list every output in its group, marking the ready ones", () => {
    const groups = outputGroups(outputs, "English");
    expect(groups.map((group) => group.name)).toEqual(["Analysis", "Smart Recap", "Repurposing"]);
    expect(groups.flatMap((group) => group.items).length).toBe(OUTPUTS.length);
    expect(groups[0].items).toEqual([
      { key: "overall", label: "Overall analysis", ready: true },
      { key: "deep", label: "Deep analysis", ready: false },
    ]);
  });

  it("open the first output for an unknown key, and map the old routes", () => {
    expect(outputFor("nope").key).toBe("overall");
    expect(LEGACY_OUTPUTS).toEqual({ analysis: "overall", "smart-recap": "recap-professional", "content-repurposing": "summary" });
    expect(Object.values(LEGACY_OUTPUTS).every((key) => outputFor(key).key === key)).toBe(true);
  });
});

describe("the outputs' Markdown", () => {
  it("becomes headings, paragraphs with their line breaks, and lists", () => {
    const blocks = markdownBlocks("# Recap\n\nHi all,\nthanks for coming.\n\n- one\n- **two**\n\n1. first\n2. second\n---\nEnd");
    expect(blocks.map((block) => block.type)).toEqual(["heading", "paragraph", "list", "list", "paragraph"]);
    expect(blocks[0]).toEqual({ type: "heading", level: 1, parts: [{ text: "Recap" }] });
    expect(blocks[1].parts).toEqual([{ text: "Hi all,\nthanks for coming." }]);
    expect(blocks[2]).toEqual({ type: "list", ordered: false, items: [[{ text: "one" }], [{ text: "two", bold: true }]] });
    expect(blocks[3].ordered).toBe(true);
  });

  it("keeps a wrapped list item together", () => {
    const [list] = markdownBlocks("- a long item\n  that wraps");
    expect(list.items).toEqual([[{ text: "a long item" }, { text: " " }, { text: "that wraps" }]]);
  });

  it("marks bold, italic and code, and leaves anything else as text", () => {
    expect(inlineParts("a **b** _c_ `d` e")).toEqual([
      { text: "a " },
      { text: "b", bold: true },
      { text: " " },
      { text: "c", italic: true },
      { text: " " },
      { text: "d", code: true },
      { text: " e" },
    ]);
    expect(inlineParts("2 * 3 * 4")).toEqual([{ text: "2 * 3 * 4" }]);
  });

  it("never turns model text into markup", () => {
    const blocks = markdownBlocks('<img src=x onerror="alert(1)"> **<b>hi</b>**');
    expect(blocks).toEqual([
      { type: "paragraph", parts: [{ text: '<img src=x onerror="alert(1)"> ' }, { text: "<b>hi</b>", bold: true }] },
    ]);
  });
});
