import { describe, expect, it } from "vitest";
import {
  CORNER_OUTPUTS,
  LEGACY_OUTPUTS,
  OUTPUTS,
  canMake,
  cleanRecap,
  earlierVersions,
  listKeyFor,
  making,
  modelLabel,
  outputFor,
  outputGroups,
  outputText,
  pdfUrl,
  tabItems,
  tabOptions,
  toggleOptions,
  versionText,
  versionsQuery,
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
    expect(making(state, decisions, "English")).toEqual({ busy: true, progress: 30, stage: "" });
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
    expect(making(state, digest, "English")).toEqual({ busy: true, progress: 40, stage: "" });
    expect(making(state, digest, "French")).toEqual({ busy: false, progress: null, stage: "" });
    expect(pdfUrl(fakeApi, "s1", digest, "English")).toBe("");
  });

  it("read, make and follow the attendee follow-up (the email's Attended tab) by language, with no PDF", () => {
    const followup = outputFor("attendee-followup");
    const email = "Subject: Thanks for joining\n\nHi there,\n\nWatch the replay at {{replay_link}}.";
    const written = { ...outputs, attendeeFollowupBundle: { English: email } };
    expect(followup.flow).toBe("attendeeFollowup");
    expect(followup.group).toBe("Repurposing");
    expect([followup.label, followup.tabLabel]).toEqual(["Follow-up email", "Attended"]);
    expect(outputText(written, followup, "English")).toBe(email);
    expect(outputText(outputs, followup, "English")).toBe("");
    expect(canMake(outputs, followup, "French")).toBe(false);
    expect(canMake(written, followup, "French")).toBe(true);
    const state = { loading: { attendeeFollowup: true }, aiJobs: { attendee_followup: { language: "French", progress: { percent: 55 } } } };
    expect(making(state, followup, "French")).toEqual({ busy: true, progress: 55, stage: "" });
    expect(making(state, followup, "English")).toEqual({ busy: false, progress: null, stage: "" });
    expect(pdfUrl(fakeApi, "s1", followup, "English")).toBe("");
  });

  it("read, make and follow the no-show email (the Not attended tab) by language, with no PDF", () => {
    const noShow = outputFor("no-show-followup");
    const email = "Subject: The replay is ready\n\nHi there,\n\nWatch it at {{replay_link}}.";
    const written = { ...outputs, noShowFollowupBundle: { English: email } };
    expect([noShow.flow, noShow.group, noShow.label, noShow.tabLabel]).toEqual(["noShowFollowup", "Repurposing", "Follow-up email", "Not attended"]);
    expect(outputText(written, noShow, "English")).toBe(email);
    expect(outputText(outputs, noShow, "English")).toBe("");
    expect(canMake(outputs, noShow, "French")).toBe(false);
    expect(canMake(written, noShow, "French")).toBe(true);
    const state = { loading: { noShowFollowup: true }, aiJobs: { no_show_followup: { language: "English", progress: { percent: 30 } } } };
    expect(making(state, noShow, "English")).toEqual({ busy: true, progress: 30, stage: "" });
    expect(making(state, outputFor("attendee-followup"), "English").busy).toBe(false);
    expect(making(state, noShow, "French").busy).toBe(false);
    expect(pdfUrl(fakeApi, "s1", noShow, "English")).toBe("");
    expect(versionsQuery(noShow, "French")).toEqual({ kind: "no_show_followup", language: "French" });
  });

  it("show the Follow-up email as three tabs, each with its purpose, button and URL key", () => {
    const tabs = tabOptions(outputFor("email"));
    expect(tabs.map((tab) => [tab.key, tab.tabLabel])).toEqual([
      ["attendee-followup", "Attended"],
      ["no-show-followup", "Not attended"],
      ["email", "Generic"],
    ]);
    // Old links keep their tab: the attendee follow-up's and the repurposed email's keys.
    expect(outputFor("attendee-followup").tabLabel).toBe("Attended");
    expect(outputFor("email").tabLabel).toBe("Generic");
    expect(outputFor("email").item).toBe("email");
    for (const tab of tabs) {
      expect(tab.label).toBe("Follow-up email");
      expect(tab.purpose).toMatch(/^(For the people who came|For the people who registered and didn't come|Works for both)/);
      expect(listKeyFor(tab)).toBe("attendee-followup");
    }
    expect(outputFor("attendee-followup").makeLabel).toBe("Write the email for attendees");
    expect(outputFor("no-show-followup").makeLabel).toBe("Write the email for people who didn't attend");
    expect(outputFor("email").makeLabel).toBeUndefined();
    expect(outputFor("no-show-followup").notYet).toContain("never named");
    expect(tabOptions(outputFor("overall"))).toEqual([]);
    expect(toggleOptions(outputFor("email"))).toEqual([], "tabs are not the recap's toggle");
    expect(listKeyFor(outputFor("recap-hype"))).toBe("recap-professional");
    expect(listKeyFor(outputFor("faq"))).toBe("faq");

    const written = { ...outputs, noShowFollowupBundle: { English: "Subject: Hi" } };
    const state = { loading: { attendeeFollowup: true }, aiJobs: { attendee_followup: { language: "English" } } };
    expect(tabItems(outputFor("no-show-followup"), written, state, "English")).toEqual([
      { key: "attendee-followup", label: "Attended", purpose: tabs[0].purpose, current: false, ready: false, writing: true },
      { key: "no-show-followup", label: "Not attended", purpose: tabs[1].purpose, current: true, ready: true, writing: false },
      { key: "email", label: "Generic", purpose: tabs[2].purpose, current: false, ready: true, writing: false },
    ]);
    expect(tabItems(outputFor("overall"), written, state, "English")).toEqual([]);
  });

  it("read, make and follow the FAQ by language, with no PDF", () => {
    const faq = outputFor("faq");
    const withFaq = { ...outputs, faqBundle: { English: "## Questions and answers\n### Is there an API?\nYes." } };
    expect(outputText(withFaq, faq, "English")).toBe("## Questions and answers\n### Is there an API?\nYes.");
    expect(outputText(outputs, faq, "English")).toBe("");
    expect(canMake(outputs, faq, "French")).toBe(false);
    expect(canMake(withFaq, faq, "French")).toBe(true);
    const state = { loading: { faq: true }, aiJobs: { faq: { language: "English", progress: { percent: 10 } } } };
    expect(making(state, faq, "English")).toEqual({ busy: true, progress: 10, stage: "" });
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
    expect(making(state, outputFor("overall"), "French")).toEqual({ busy: true, progress: 43, stage: "" });
    expect(making(state, outputFor("overall"), "English")).toEqual({ busy: false, progress: null, stage: "" });
    expect(making(state, outputFor("summary"), "French")).toEqual({ busy: false, progress: null, stage: "" });
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

  it("list every output in its group, the recap's two tones and the email's three tabs as one item each", () => {
    const groups = outputGroups(outputs, "English");
    // The FAQ is filed with the desk ("From the session"); the view puts that group first.
    expect(groups.map((group) => group.name)).toEqual(["Analysis", "Repurposing", "From the session"]);
    expect(OUTPUTS.some((output) => output.group === "For the audience")).toBe(false);
    // Surprise me sits in its own corner, not in a group.
    expect(CORNER_OUTPUTS.map((output) => output.key)).toEqual(["recap-surprise"]);
    expect(groups.flatMap((group) => group.items).some((item) => item.key === "recap-surprise")).toBe(false);
    expect(groups[0].items).toEqual([
      { key: "overall", label: "Session analysis", ready: true, keys: ["overall"], toggle: "" },
      { key: "decisions", label: "Decisions and commitments", ready: false, keys: ["decisions"], toggle: "" },
      { key: "team-digest", label: "Team digest", ready: false, keys: ["team-digest"], toggle: "" },
      { key: "recap-professional", label: "Smart Recap", ready: true, keys: ["recap-professional", "recap-hype"], toggle: "recap" },
    ]);
    expect(groups[1].items.map((item) => item.label)).toEqual(["Summary", "Blog post", "Follow-up email", "Social posts"]);
    expect(groups[1].items[2]).toEqual({
      key: "attendee-followup",
      label: "Follow-up email",
      ready: true,
      keys: ["attendee-followup", "no-show-followup", "email"],
      toggle: "email",
    });
    expect(groups[2].items.map((item) => item.key)).toEqual(["faq"]);
  });

  it("switch the recap between Formal and Hype, and keep the Deep analysis read-only", () => {
    expect(toggleOptions(outputFor("recap-hype")).map((item) => [item.key, item.toggleLabel])).toEqual([
      ["recap-professional", "Formal"],
      ["recap-hype", "Hype"],
    ]);
    expect(toggleOptions(outputFor("overall"))).toEqual([]);
    const deep = outputFor("deep");
    expect([deep.group, deep.archived, deep.flow]).toEqual(["Earlier outputs", true, undefined]);
    expect(canMake(outputs, deep, "English")).toBe(false);
    expect(making({ loading: { undefined: true } }, deep, "English").busy).toBe(false);
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

describe("earlier outputs and earlier versions", () => {
  it("list the Deep analysis only when one was saved, in any language", () => {
    expect(outputGroups(outputs, "English").some((group) => group.name === "Earlier outputs")).toBe(false);
    const saved = { ...outputs, deepAnalysisBundle: { French: "## Analyse" } };
    const archive = outputGroups(saved, "English").find((group) => group.name === "Earlier outputs");
    expect(archive.items).toEqual([{ key: "deep", label: "Deep analysis", ready: false, keys: ["deep"], toggle: "" }]);
    expect(pdfUrl(fakeApi, "s1", outputFor("deep"), "French")).toBe("/a/s1/deep/French");
  });

  it("ask the server for each output's versions by language or tone", () => {
    expect(versionsQuery(outputFor("overall"), "French")).toEqual({ kind: "overall", language: "French" });
    expect(versionsQuery(outputFor("recap-hype"), "French")).toEqual({ kind: "smart_recap", tone: "hype" });
    expect(versionsQuery(outputFor("blog"), "English")).toEqual({ kind: "content", language: "English" });
    expect(versionsQuery(outputFor("team-digest"), "English")).toEqual({ kind: "team_digest", language: "English" });
    expect(versionsQuery(outputFor("attendee-followup"), "French")).toEqual({ kind: "attendee_followup", language: "French" });
    expect(versionsQuery(outputFor("deep"), "English")).toEqual({ kind: "deep", language: "English" });
  });

  it("read a version as the output shows it", () => {
    expect(versionText(outputFor("blog"), { summary: "S", blog: " Long. " })).toBe("Long.");
    expect(versionText(outputFor("blog"), "not a bundle")).toBe("");
    expect(versionText(outputFor("recap-professional"), "# Title\nRecap\n# Description\nBody")).toBe("## Recap\nBody");
    expect(versionText(outputFor("faq"), { not: "text" })).toBe("");
    expect(modelLabel("claude-sonnet-5-5")).toBe("Claude Sonnet 5.5");
    expect(modelLabel("gpt-image-1")).toBe("gpt-image-1");
  });

  it("list every version but the one on screen, without repeats or empty ones", () => {
    const body = {
      versions: [
        { id: 4, createdAt: "2026-11-11T13:02:00Z", models: ["claude-sonnet-5-5"], content: { blog: "Now" } },
        { id: 3, createdAt: "2026-11-10T09:00:00Z", models: ["claude-sonnet-5-5", "claude-sonnet-5-5"], content: { blog: "Before" } },
        { id: 2, createdAt: "2026-11-09T09:00:00Z", models: [], content: { blog: "Before" } },
        { id: 1, createdAt: "2026-11-08T09:00:00Z", models: ["claude-haiku-5-5"], content: { summary: "no blog" } },
      ],
    };
    const listed = earlierVersions(body, outputFor("blog"), "Now");
    expect(listed.map((v) => [v.id, v.text, v.models])).toEqual([[3, "Before", "Claude Sonnet 5.5"]]);
    expect(listed[0].when).toMatch(/10 Nov 2026/);
    expect(earlierVersions(null, outputFor("blog"), "Now")).toEqual([]);
  });
});

describe("an output being written", () => {
  it("says which stage the worker is at", () => {
    const state = { loading: { faq: true }, aiJobs: { faq: { language: "English", progress: { percent: 40, stage: "analyzing" } } } };
    expect(making(state, outputFor("faq"), "English")).toEqual({ busy: true, progress: 40, stage: "analyzing" });
  });
});
