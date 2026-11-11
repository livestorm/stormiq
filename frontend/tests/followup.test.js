import { describe, expect, it } from "vitest";
import {
  CORNER_OUTPUTS,
  LEGACY_OUTPUTS,
  OUTPUTS,
  canMake,
  cleanRecap,
  making,
  outputFor,
  outputGroups,
  outputText,
  pdfUrl,
  toggleOptions,
} from "../src/followup/outputs";
import { inlineParts, markdownBlocks, markdownSections, plainText } from "../src/followup/markdown";

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
    expect(outputText(outputs, outputFor("email"), "English")).toBe("Hi all,");
    expect(outputText(outputs, outputFor("recap-professional"), "French")).toBe("## Spring launch recap\nThe session, in short.");
    expect(outputText(outputs, outputFor("recap-hype"), "English")).toBe("");
  });

  it("read, make and follow the decisions by language, with no PDF", () => {
    const decisions = outputFor("decisions");
    const listed = { ...outputs, decisionsBundle: { English: "## Commitments\n\n- **Send the slides.**" } };
    expect(decisions.flow).toBe("decisions");
    expect(outputText(listed, decisions, "English")).toBe("## Commitments\n\n- **Send the slides.**");
    expect(outputText(outputs, decisions, "English")).toBe("");
    expect(canMake(outputs, decisions, "French")).toBe(false);
    expect(canMake(listed, decisions, "French")).toBe(true);
    const state = { loading: { decisions: true }, aiJobs: { decisions: { language: "English", progress: { percent: 30 } } } };
    expect(making(state, decisions, "English")).toEqual({ busy: true, progress: 30 });
    expect(pdfUrl(fakeApi, "s1", decisions, "English")).toBe("");
  });

  it("read, make and follow the team digest by language, with no PDF", () => {
    const digest = outputFor("team-digest");
    const written = { ...outputs, teamDigestBundle: { English: "*Internal: not for sharing outside the team.*\n- 5 questions." } };
    expect(digest.flow).toBe("teamDigest");
    expect(outputText(written, digest, "English")).toBe("*Internal: not for sharing outside the team.*\n- 5 questions.");
    expect(outputText(outputs, digest, "English")).toBe("");
    expect(canMake(outputs, digest, "French")).toBe(false);
    expect(canMake(written, digest, "French")).toBe(true);
    const state = { loading: { teamDigest: true }, aiJobs: { team_digest: { language: "English", progress: { percent: 40 } } } };
    expect(making(state, digest, "English")).toEqual({ busy: true, progress: 40 });
    expect(making(state, digest, "French")).toEqual({ busy: false, progress: null });
    expect(pdfUrl(fakeApi, "s1", digest, "English")).toBe("");
  });

  it("read, make and follow the FAQ by language, with no PDF", () => {
    const faq = outputFor("faq");
    const withFaq = { ...outputs, faqBundle: { English: "## Questions and answers\n### Is there an API?\nYes." } };
    expect(outputText(withFaq, faq, "English")).toBe("## Questions and answers\n### Is there an API?\nYes.");
    expect(outputText(outputs, faq, "English")).toBe("");
    expect(canMake(outputs, faq, "French")).toBe(false);
    expect(canMake(withFaq, faq, "French")).toBe(true);
    const state = { loading: { faq: true }, aiJobs: { faq: { language: "English", progress: { percent: 10 } } } };
    expect(making(state, faq, "English")).toEqual({ busy: true, progress: 10 });
    expect(pdfUrl(fakeApi, "s1", faq, "English")).toBe("");
  });

  it("clean a recap's Title and Description labels as the classic view did", () => {
    expect(cleanRecap("## title\nA day\n## Description\nBody")).toBe("## A day\nBody");
    expect(cleanRecap("")).toBe("");
  });

  it("let French be written only once the English one exists", () => {
    expect(canMake(outputs, outputFor("overall"), "French")).toBe(true);
    expect(canMake({}, outputFor("blog"), "French")).toBe(false);
    expect(canMake({}, outputFor("blog"), "English")).toBe(true);
    expect(canMake({}, outputFor("recap-hype"), "French")).toBe(true);
  });

  it("say an output is being written only for its own slot", () => {
    const state = { loading: { analysis: true }, aiJobs: { overall_analysis: { language: "French", progress: { percent: 42.6 } } } };
    expect(making(state, outputFor("overall"), "French")).toEqual({ busy: true, progress: 43 });
    expect(making(state, outputFor("overall"), "English")).toEqual({ busy: false, progress: null });
    expect(making(state, outputFor("summary"), "French")).toEqual({ busy: false, progress: null });
    const recap = { loading: { smartRecap: true }, aiJobs: { smart_recap: { tone: "hype" } } };
    expect(making(recap, outputFor("recap-hype"), "English").busy).toBe(true);
    expect(making(recap, outputFor("recap-professional"), "English").busy).toBe(false);
  });

  it("point each output at its PDF", () => {
    expect(pdfUrl(fakeApi, "s1", outputFor("overall"), "French")).toBe("/a/s1/overall/French");
    expect(pdfUrl(fakeApi, "s1", outputFor("recap-surprise"), "English")).toBe("/r/s1/surprise");
    expect(pdfUrl(fakeApi, "s1", outputFor("social"), "English")).toBe("/c/s1/English/social_media");
    expect(pdfUrl(fakeApi, "", outputFor("overall"), "English")).toBe("");
  });

  it("list every output in its group, the recap's two tones as one item", () => {
    const groups = outputGroups(outputs, "English");
    expect(groups.map((group) => group.name)).toEqual(["Analysis", "Repurposing"]);
    // Surprise me sits in its own corner, not in a group.
    expect(CORNER_OUTPUTS.map((output) => output.key)).toEqual(["recap-surprise"]);
    expect(groups.flatMap((group) => group.items).some((item) => item.key === "recap-surprise")).toBe(false);
    expect(groups[0].items).toEqual([
      { key: "overall", label: "Session analysis", ready: true, keys: ["overall"], toggle: "" },
      { key: "decisions", label: "Decisions and commitments", ready: false, keys: ["decisions"], toggle: "" },
      { key: "team-digest", label: "Team digest", ready: false, keys: ["team-digest"], toggle: "" },
      { key: "recap-professional", label: "Smart Recap", ready: true, keys: ["recap-professional", "recap-hype"], toggle: "recap" },
    ]);
    expect(groups[1].items.map((item) => item.label)).toEqual(["Summary", "Blog post", "Follow-up email", "Social posts", "FAQ"]);
  });

  it("switch the recap between Formal and Hype, and leave the Deep analysis off the list", () => {
    expect(toggleOptions(outputFor("recap-hype")).map((item) => [item.key, item.toggleLabel])).toEqual([
      ["recap-professional", "Formal"],
      ["recap-hype", "Hype"],
    ]);
    expect(toggleOptions(outputFor("overall"))).toEqual([]);
    expect(OUTPUTS.some((output) => output.key === "deep")).toBe(false);
    expect(outputFor("deep").key).toBe("overall", "an old link opens the Session analysis");
    expect(OUTPUTS.filter((output) => output.sections).map((output) => output.key)).toEqual(["email", "social"]);
  });

  it("open the first output for an unknown key, and map the old routes", () => {
    expect(outputFor("nope").key).toBe("overall");
    expect(LEGACY_OUTPUTS).toEqual({ analysis: "overall", "smart-recap": "recap-professional", "content-repurposing": "summary" });
    expect(Object.values(LEGACY_OUTPUTS).every((key) => outputFor(key).key === key)).toBe(true);
  });
});

describe("the outputs' sections and plain text", () => {
  const social = "# LinkedIn\nWe walked **70 people** through it.\n\n#SaaS #Webinar\n\n# X / Twitter\nNew dashboard. #SaaS";

  it("cut a post list at its top-level headings", () => {
    expect(markdownSections(social)).toEqual([
      { title: "LinkedIn", text: "We walked **70 people** through it.\n\n#SaaS #Webinar" },
      { title: "X / Twitter", text: "New dashboard. #SaaS" },
    ]);
    expect(markdownSections("Intro\n## A\none\n### deeper\ntwo\n## B\nthree").map((s) => s.title)).toEqual(["", "A", "B"]);
    expect(markdownSections("No headings.")).toEqual([{ title: "", text: "No headings." }]);
    expect(markdownSections("  ")).toEqual([]);
  });

  it("copy a part as plain text, without Markdown marks", () => {
    expect(plainText("We walked **70 people** through it.\n\n#SaaS #Webinar")).toBe("We walked 70 people through it.\n\n#SaaS #Webinar");
    expect(plainText("Hi,\nthanks.\n\n- one\n- _two_\n\n1. a\n2. b")).toBe("Hi,\nthanks.\n\n• one\n• two\n\n1. a\n2. b");
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
