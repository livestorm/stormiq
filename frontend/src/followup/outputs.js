// The Follow-up tab's outputs: what each AI output is, where it lives in the
// classic workspace (`workspace.outputs`, docs/ai-outputs.md) and how it is
// made. Pure functions over the workspace and the store's job slots; the
// store's AI flows (`runAnalysis`, `runDeepAnalysis`, `runSmartRecap`,
// `runContentRepurposing`) make them, each only on a click.

export const LANGUAGES = [
  { value: "English", label: "English" },
  { value: "French", label: "Français" },
];

// One entry per output the page lists. `flow` names the store action and its
// loading flag and job slot; `bundle` is the workspace output; `by` is what
// the bundle is keyed by (a language, or a tone); `item` is a repurposing
// item inside a language.
export const OUTPUTS = [
  {
    key: "overall",
    group: "Analysis",
    label: "Overall analysis",
    about: "What happened in the session and what to do next, in one read.",
    flow: "analysis",
    bundle: "analysisBundle",
    by: "language",
    pdf: { kind: "overall" },
  },
  {
    key: "deep",
    group: "Analysis",
    label: "Deep analysis",
    about: "Scores, key moments, speakers, the audience's intent, risks and recommendations.",
    flow: "deepAnalysis",
    bundle: "deepAnalysisBundle",
    by: "language",
    pdf: { kind: "deep" },
  },
  {
    key: "recap-professional",
    group: "Smart Recap",
    label: "Recap: professional",
    about: "A polished recap for internal sharing or stakeholder readouts.",
    flow: "smartRecap",
    bundle: "smartRecapBundle",
    by: "tone",
    tone: "professional",
  },
  {
    key: "recap-hype",
    group: "Smart Recap",
    label: "Recap: hype",
    about: "A more energetic version that keeps momentum and punch.",
    flow: "smartRecap",
    bundle: "smartRecapBundle",
    by: "tone",
    tone: "hype",
  },
  {
    key: "recap-surprise",
    group: "Smart Recap",
    label: "Recap: surprise me",
    about: "An unexpected angle that still stays grounded in the transcript.",
    flow: "smartRecap",
    bundle: "smartRecapBundle",
    by: "tone",
    tone: "surprise",
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
    key: "email",
    group: "Repurposing",
    label: "Follow-up email",
    about: "An email to send the attendees.",
    flow: "contentRepurposing",
    bundle: "contentRepurposeBundle",
    by: "language",
    item: "email",
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
  },
];

const BY_KEY = new Map(OUTPUTS.map((output) => [output.key, output]));

// The store's job slot for each flow (`state.aiJobs[jobKind]`).
const JOB_KIND = {
  analysis: "overall_analysis",
  deepAnalysis: "deep_analysis",
  smartRecap: "smart_recap",
  contentRepurposing: "content_repurposing",
};

/** The output for a key (from a URL), or the first one. */
export function outputFor(key) {
  return BY_KEY.get(key) || OUTPUTS[0];
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
  if (output.by === "tone" || language === "English") return true;
  return Boolean(outputText(outputs, output, "English"));
}

/** Whether this output is being made now, and its progress (0–100 or
 * null), from the store's loading flag and job slot. */
export function making(state, output, language) {
  if (!state?.loading?.[output.flow]) return { busy: false, progress: null };
  const job = state.aiJobs?.[JOB_KIND[output.flow]];
  const sameSlot = output.by === "tone" ? !job?.tone || job.tone === output.tone : !job?.language || job.language === language;
  if (!sameSlot) return { busy: false, progress: null };
  const percent = Number(job?.progress?.percent);
  return { busy: true, progress: Number.isFinite(percent) ? Math.max(0, Math.min(100, Math.round(percent))) : null };
}

/** The PDF's address for an output that has one, else "". */
export function pdfUrl(api, sessionId, output, language) {
  if (!sessionId) return "";
  if (output.pdf) return api.getAnalysisPdfUrl(sessionId, output.pdf.kind, language);
  if (output.by === "tone") return api.getSmartRecapPdfUrl(sessionId, output.tone);
  if (output.item) return api.getContentRepurposingPdfUrl(sessionId, language, output.item);
  return "";
}

/** The list on the left: groups of `{key, label, ready}`, `ready` when the
 * output exists in `language` (a recap in any). */
export function outputGroups(outputs, language) {
  const groups = [];
  for (const output of OUTPUTS) {
    let group = groups.find((item) => item.name === output.group);
    if (!group) {
      group = { name: output.group, items: [] };
      groups.push(group);
    }
    group.items.push({ key: output.key, label: output.label, ready: Boolean(outputText(outputs, output, language)) });
  }
  return groups;
}
