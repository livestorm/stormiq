// The Follow-up tab's outputs: what each AI output is, where it lives in the
// classic workspace (`workspace.outputs`, docs/ai-outputs.md) and how it is
// made. Pure functions over the workspace and the store's job slots; the
// store's AI flows (`runAnalysis`, `runDeepAnalysis`, `runSmartRecap`,
// `runContentRepurposing`) make them, each only on a click.

export const LANGUAGES = [
  { value: "English", label: "English" },
  { value: "French", label: "Français" },
];

// One entry per output the page shows. `flow` names the store action and its
// loading flag and job slot; `bundle` is the workspace output; `by` is what
// the bundle is keyed by (a language, or a tone); `item` is a repurposing
// item inside a language. Entries that share a `toggle` are one item in the
// list, switched by a toggle on the output (`toggleLabel`): the Smart
// Recap's Formal and Hype. Entries that share `tabs` are one item too, shown
// as tabs on the output, each with its own language, button, loader and
// versions (`tabLabel`, `purpose`: one line on who it is for, `makeLabel`,
// `notYet`): the Follow-up email's Attended, Not attended and Generic, each
// keeping its own `?output=` key (`attendee-followup` and `email` from before
// the tabs, `no-show-followup`). `sections`: each top-level section (a post, an
// email) gets its own Copy. `archived`: an output StormIQ no longer makes,
// kept readable under "Earlier outputs" when one was saved (the Deep
// analysis, off the list since November 2026: the owner's decision); it has
// no flow and no button, and shows in the list only when it exists.
// The Follow-up email's tabs share its title and this line.
const EMAIL_ABOUT = "The email to send after the session, in three versions: pick who it is for.";
const NEVER_NAMED =
  "It holds a {{replay_link}} for your email tool to fill in. Attendees are never named: their names, emails and links are removed before anything is sent. An email with a number or a time the session doesn't have is never saved.";

export const OUTPUTS = [
  {
    key: "overall",
    group: "Analysis",
    label: "Session analysis",
    about: "What happened, what landed with the audience, the questions, and what to do next.",
    flow: "analysis",
    bundle: "analysisBundle",
    by: "language",
    pdf: { kind: "overall" },
  },
  {
    key: "decisions",
    group: "Analysis",
    label: "Decisions and commitments",
    about: "What was decided on stage and who promised to do what, with the words said and when, for the team's notes.",
    flow: "decisions",
    bundle: "decisionsBundle",
    by: "language",
  },
  {
    key: "team-digest",
    group: "Analysis",
    label: "Team digest",
    about: "About 100 words for the team's Slack or Teams channel, marked internal: the numbers with their times, and the decisions.",
    flow: "teamDigest",
    bundle: "teamDigestBundle",
    by: "language",
  },
  {
    key: "recap-professional",
    group: "Analysis",
    label: "Smart Recap",
    about: "A short recap of the session, ready to share.",
    flow: "smartRecap",
    bundle: "smartRecapBundle",
    by: "tone",
    tone: "professional",
    toggle: "recap",
    toggleLabel: "Formal",
  },
  {
    key: "recap-hype",
    group: "Analysis",
    label: "Smart Recap",
    about: "A short recap of the session, ready to share.",
    flow: "smartRecap",
    bundle: "smartRecapBundle",
    by: "tone",
    tone: "hype",
    toggle: "recap",
    toggleLabel: "Hype",
  },
  {
    key: "recap-surprise",
    group: "Smart Recap",
    label: "Surprise me",
    about: "The session as a short show: an unexpected take, performed by an AI voice over the session's own numbers and chat. Just for fun.",
    flow: "smartRecap",
    bundle: "smartRecapBundle",
    by: "tone",
    tone: "surprise",
    // In its own small corner of the list, not with the outputs to share.
    corner: true,
  },
  {
    key: "summary",
    group: "Repurposing",
    label: "Summary",
    about: "A short written summary of the session.",
    flow: "contentRepurposing",
    bundle: "contentRepurposeBundle",
    by: "language",
    item: "summary",
  },
  {
    key: "blog",
    group: "Repurposing",
    label: "Blog post",
    about: "The session as an article.",
    flow: "contentRepurposing",
    bundle: "contentRepurposeBundle",
    by: "language",
    item: "blog",
  },
  {
    key: "attendee-followup",
    group: "Repurposing",
    label: "Follow-up email",
    about: EMAIL_ABOUT,
    tabs: "email",
    tabLabel: "Attended",
    purpose:
      "For the people who came: \"You asked, we answered\", with what the session covered, three takeaways and two of their questions answered.",
    makeLabel: "Write the email for attendees",
    notYet: `Not written yet. It runs only when you ask, with AI, from the session's chapters, the key moments of the talk and two questions the audience asked that have an answer. ${NEVER_NAMED}`,
    flow: "attendeeFollowup",
    bundle: "attendeeFollowupBundle",
    by: "language",
  },
  {
    key: "no-show-followup",
    group: "Repurposing",
    label: "Follow-up email",
    about: EMAIL_ABOUT,
    tabs: "email",
    tabLabel: "Not attended",
    purpose: "For the people who registered and didn't come: what they missed, a question or two answered, and a reason to watch the replay.",
    makeLabel: "Write the email for people who didn't attend",
    notYet: `Not written yet. It runs only when you ask, with AI, from the session's chapters, the key moments of the talk and up to two questions the audience asked that have an answer. ${NEVER_NAMED}`,
    flow: "noShowFollowup",
    bundle: "noShowFollowupBundle",
    by: "language",
  },
  {
    key: "email",
    group: "Repurposing",
    label: "Follow-up email",
    about: EMAIL_ABOUT,
    tabs: "email",
    tabLabel: "Generic",
    purpose: "Works for both: one email you can send everyone who registered, whether they came or not.",
    flow: "contentRepurposing",
    bundle: "contentRepurposeBundle",
    by: "language",
    item: "email",
    sections: true,
  },
  {
    key: "social",
    group: "Repurposing",
    label: "Social posts",
    about: "Posts for social media.",
    flow: "contentRepurposing",
    bundle: "contentRepurposeBundle",
    by: "language",
    item: "social_media",
    sections: true,
  },
  {
    key: "faq",
    group: "Repurposing",
    label: "FAQ",
    about: "The audience's questions and their answers, for the replay page or the follow-up email. Who asked is never named.",
    flow: "faq",
    bundle: "faqBundle",
    by: "language",
  },
  {
    key: "deep",
    group: "Earlier outputs",
    label: "Deep analysis",
    about: "The detailed analysis StormIQ used to write. It is no longer made; this is the one saved for this session.",
    bundle: "deepAnalysisBundle",
    by: "language",
    pdf: { kind: "deep" },
    archived: true,
  },
];

const BY_KEY = new Map(OUTPUTS.map((output) => [output.key, output]));

// The store's job slot for each flow (`state.aiJobs[jobKind]`).
const JOB_KIND = {
  analysis: "overall_analysis",
  faq: "faq",
  decisions: "decisions",
  teamDigest: "team_digest",
  attendeeFollowup: "attendee_followup",
  noShowFollowup: "no_show_followup",
  smartRecap: "smart_recap",
  contentRepurposing: "content_repurposing",
};

/** The output for a key (from a URL), or the first one (also for an output
 * that left the list, such as the Deep analysis). */
export function outputFor(key) {
  return BY_KEY.get(key) || OUTPUTS[0];
}

/** The outputs one toggle switches between, in order (`[]` without one). */
export function toggleOptions(output) {
  return output?.toggle ? OUTPUTS.filter((item) => item.toggle === output.toggle) : [];
}

/** The outputs one item's tabs switch between, in order (`[]` without tabs). */
export function tabOptions(output) {
  return output?.tabs ? OUTPUTS.filter((item) => item.tabs === output.tabs) : [];
}

/** The key the list and the phone picker use for an output: its toggle's or
 * its tabs' first, else its own. */
export function listKeyFor(output) {
  return toggleOptions(output)[0]?.key || tabOptions(output)[0]?.key || output.key;
}

/** The old classic routes, as the Follow-up output to open. */
export const LEGACY_OUTPUTS = { analysis: "overall", "smart-recap": "recap-professional", "content-repurposing": "summary" };

function text(value) {
  return String(value ?? "").trim();
}

/** A Smart Recap's text as the classic view showed it: its "Title" label
 * dropped (the title becomes a heading), its "Description" label dropped. */
export function cleanRecap(value) {
  const lines = text(value).replace(/\r\n/g, "\n").split("\n");
  const out = [];
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index].trim();
    if (/^#{1,2}\s*title\s*$/i.test(line)) {
      const next = text(lines[index + 1]);
      if (next) {
        out.push(`## ${next}`);
        index += 1;
      }
      continue;
    }
    if (/^#{1,2}\s*description\s*$/i.test(line)) continue;
    out.push(lines[index]);
  }
  return out.join("\n").trim();
}

/** The text of one output in one language ("" when not made). A recap has
 * no language. */
export function outputText(outputs, output, language) {
  const bundle = outputs?.[output.bundle] || {};
  if (output.by === "tone") return cleanRecap(bundle[output.tone]);
  const inLanguage = bundle[language];
  if (output.item) return text(inLanguage?.[output.item]);
  return text(inLanguage);
}

/** Whether `language` may be made for this output: English always; French
 * once the English one exists (the server writes the French version from
 * it, as the classic view did). A recap has no language. */
export function canMake(outputs, output, language) {
  if (output.archived) return false;
  if (output.by === "tone" || language === "English") return true;
  return Boolean(outputText(outputs, output, "English"));
}

/** Whether this output is being made now, its progress (0–100 or null)
 * and the worker's stage ("analyzing", or "" before it says), from the
 * store's loading flag and job slot. */
export function making(state, output, language) {
  if (!output.flow || !state?.loading?.[output.flow]) return { busy: false, progress: null, stage: "" };
  const job = state.aiJobs?.[JOB_KIND[output.flow]];
  const sameSlot = output.by === "tone" ? !job?.tone || job.tone === output.tone : !job?.language || job.language === language;
  if (!sameSlot) return { busy: false, progress: null, stage: "" };
  const percent = Number(job?.progress?.percent);
  return {
    busy: true,
    progress: Number.isFinite(percent) ? Math.max(0, Math.min(100, Math.round(percent))) : null,
    stage: typeof job?.progress?.stage === "string" ? job.progress.stage : "",
  };
}

/** The PDF's address for an output that has one, else "". */
export function pdfUrl(api, sessionId, output, language) {
  if (!sessionId) return "";
  if (output.pdf) return api.getAnalysisPdfUrl(sessionId, output.pdf.kind, language);
  if (output.by === "tone") return api.getSmartRecapPdfUrl(sessionId, output.tone);
  if (output.item) return api.getContentRepurposingPdfUrl(sessionId, language, output.item);
  return "";
}

/** The outputs shown in the list's corner (Surprise me), apart from the groups. */
export const CORNER_OUTPUTS = OUTPUTS.filter((output) => output.corner);

/** Whether an output exists, for the list's "Ready". */
export function outputReady(outputs, output, language) {
  return Boolean(outputText(outputs, output, language));
}

/** The tabs of the output on screen (the Follow-up email's Attended, Not
 * attended and Generic), each `{key, label, purpose, current, ready,
 * writing}` in `language`; `[]` for an output without tabs. Each tab is
 * its own output: its own text, button, job and versions. */
export function tabItems(output, outputs, state, language) {
  return tabOptions(output).map((tab) => ({
    key: tab.key,
    label: tab.tabLabel,
    purpose: tab.purpose,
    current: tab.key === output.key,
    ready: outputReady(outputs, tab, language),
    writing: making(state, tab, language).busy,
  }));
}

/** The list on the left: groups of `{key, label, ready, keys}`, `ready`
 * when the output exists in `language` (a recap in any). Outputs that share
 * a toggle or tabs are one item: `key` its first, `keys` all of them (for
 * the current mark), `ready` when any is; `toggle` names what they share.
 * The corner's outputs are not in a group (`CORNER_OUTPUTS`). */
export function outputGroups(outputs, language) {
  const groups = [];
  for (const output of OUTPUTS) {
    if (output.corner) continue;
    if (output.archived && !LANGUAGES.some((option) => outputText(outputs, output, option.value))) continue;
    let group = groups.find((item) => item.name === output.group);
    if (!group) {
      group = { name: output.group, items: [] };
      groups.push(group);
    }
    const ready = Boolean(outputText(outputs, output, language));
    const together = output.toggle || output.tabs || "";
    const shared = together ? group.items.find((item) => item.toggle === together) : null;
    if (shared) {
      shared.keys.push(output.key);
      shared.ready = shared.ready || ready;
      continue;
    }
    group.items.push({ key: output.key, label: output.label, ready, keys: [output.key], toggle: together });
  }
  return groups;
}

// ── Earlier versions (`GET /output-versions`) ──

// The server's kind for each flow (`api_logic.OUTPUT_VERSION_KINDS`).
const VERSION_KIND = {
  analysis: "overall",
  smartRecap: "smart_recap",
  contentRepurposing: "content",
  faq: "faq",
  decisions: "decisions",
  teamDigest: "team_digest",
  attendeeFollowup: "attendee_followup",
  noShowFollowup: "no_show_followup",
};

/** The query for an output's saved versions, or null for one without:
 * `{kind, language}`, or `{kind, tone}` for a recap. */
export function versionsQuery(output, language) {
  const kind = output.archived && output.key === "deep" ? "deep" : VERSION_KIND[output.flow];
  if (!kind) return null;
  return output.by === "tone" ? { kind, tone: output.tone } : { kind, language };
}

/** One saved version's text, as the output shows it: a repurposing item
 * from its bundle, a recap cleaned as the classic view did. */
export function versionText(output, content) {
  if (output.item) return text(content && typeof content === "object" ? content[output.item] : "");
  if (output.by === "tone") return cleanRecap(content);
  return text(typeof content === "string" ? content : "");
}

const MODEL_NAME = /^claude-([a-z]+)-(\d+)-(\d+)$/;

/** "claude-sonnet-5-5" as "Claude Sonnet 5.5"; another id as it is. */
export function modelLabel(id) {
  const found = MODEL_NAME.exec(String(id || ""));
  if (!found) return String(id || "");
  return `Claude ${found[1][0].toUpperCase()}${found[1].slice(1)} ${found[2]}.${found[3]}`;
}

/** When a version was saved, as "14 Oct 2026, 15:02" in the viewer's time. */
export function versionWhen(iso, locale = "en-GB") {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString(locale, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** The versions to list under an output: every saved one but the one on
 * screen (`currentText`), newest first, without a repeat of the version
 * just after it (a content bundle saves its four parts at once, so an item
 * can be the same across saves) or an empty one. Each `{id, when, models,
 * text}`. */
export function earlierVersions(body, output, currentText) {
  const shown = text(currentText);
  const out = [];
  let previous = null;
  for (const version of body?.versions || []) {
    const versionBody = versionText(output, version.content);
    if (!versionBody || versionBody === shown || versionBody === previous) {
      previous = versionBody || previous;
      continue;
    }
    previous = versionBody;
    out.push({
      id: version.id,
      when: versionWhen(version.createdAt),
      models: [...new Set((version.models || []).map(modelLabel).filter(Boolean))].join(", "),
      text: versionBody,
    });
  }
  return out;
}
