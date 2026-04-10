<script setup>
import { computed, ref } from "vue";
import ContentPaceAudienceActivityChartCard from "../components/charts/analysis/ContentPaceAudienceActivityChartCard.vue";
import DataTable from "../components/DataTable.vue";
import RichMarkdownCard from "../components/RichMarkdownCard.vue";
import { api } from "../api";
import { useWorkspace } from "../store/workspace";

const { state, runAnalysis, runDeepAnalysis, hasTranscriptData, isTranscriptLoading, isTranscriptUnavailable } = useWorkspace();

const activeCategory = ref("overall");
const activeOverallLanguage = ref("English");
const activeDeepLanguage = ref("English");
const activeDeepSection = ref("executive_summary");

const languageTabs = [
  { id: "English", label: "English", icon: "🇬🇧" },
  { id: "French", label: "Français", icon: "🇫🇷" },
];

const overallBundle = computed(() => state.workspace?.outputs?.analysisBundle || {});
const deepBundle = computed(() => state.workspace?.outputs?.deepAnalysisBundle || {});

const overallBody = computed(() => overallBundle.value?.[activeOverallLanguage.value] || "");
const deepBody = computed(() => deepBundle.value?.[activeDeepLanguage.value] || "");
const hasOverallBody = computed(() => Boolean(String(overallBody.value || "").trim()));
const hasDeepBody = computed(() => Boolean(String(deepBody.value || "").trim()));
const overallPdfUrl = computed(() =>
  state.workspace?.sessionId ? api.getAnalysisPdfUrl(state.workspace.sessionId, "overall", activeOverallLanguage.value) : "#"
);
const deepPdfUrl = computed(() =>
  state.workspace?.sessionId ? api.getAnalysisPdfUrl(state.workspace.sessionId, "deep", activeDeepLanguage.value) : "#"
);

const activeLanguage = computed(() => (activeCategory.value === "overall" ? activeOverallLanguage.value : activeDeepLanguage.value));
const isFrenchUi = computed(() => activeLanguage.value === "French");
const pageDescription = computed(() =>
  isFrenchUi.value
    ? "Analyse globale et approfondie générée à partir de la transcription, de la session, du chat et des questions."
    : "Overall and deeper analysis generated from transcript, session, chat, and questions."
);

const uiText = computed(() =>
  isFrenchUi.value
    ? {
        categories: {
          overall: "Analyse globale",
          deep: "Analyse approfondie",
        },
        overallButton: "Générer",
        deepButton: "Générer",
        running: "Génération...",
        downloadPdf: "Télécharger le PDF",
        empty: "Utilise la transcription pour générer l’analyse globale.",
        emptyDeep: "Utilise la transcription, la session, le chat et les questions pour générer une analyse approfondie orientée hôte.",
        contentPaceTitle: "Rythme du contenu et activité de l’audience",
        reactionMomentsTitle: "Segments avec le plus de réactions",
        reactionColumns: {
          speaker: "Intervenant",
          session_stage: "Phase de session",
          start_label: "Début",
          excerpt: "Extrait",
          chat_messages: "Messages chat",
          question_count: "Questions",
        },
      }
    : {
        categories: {
          overall: "Overall Analysis",
          deep: "Deep Analysis",
        },
        overallButton: "Generate",
        deepButton: "Generate",
        running: "Running...",
        downloadPdf: "Download PDF",
        empty: "Uses the transcript to generate the overall analysis.",
        emptyDeep: "Uses transcript, session, chat, and question signals to generate the deeper host-facing diagnostic analysis.",
        contentPaceTitle: "Content Pace And Audience Activity",
        reactionMomentsTitle: "Segments With The Most Reactions",
        reactionColumns: {
          speaker: "Speaker",
          session_stage: "Session Stage",
          start_label: "Start",
          excerpt: "Excerpt",
          chat_messages: "Chat Messages",
          question_count: "Questions",
        },
      }
);

const categories = computed(() => [
  { id: "overall", label: uiText.value.categories.overall },
  { id: "deep", label: uiText.value.categories.deep },
]);

const deepSectionLabels = computed(() =>
  isFrenchUi.value
    ? [
        { id: "executive_summary", label: "Résumé exécutif" },
        { id: "session_scores", label: "Scores de session" },
        { id: "key_moments", label: "Moments clés" },
        { id: "speaker_and_interaction_analysis", label: "Dynamique des intervenants" },
        { id: "audience_intent_analysis", label: "Intentions de l’audience" },
        { id: "cross_source_synthesis", label: "Synthèse croisée" },
        { id: "friction_and_risk_signals", label: "Friction et risques" },
        { id: "business_signals_and_kpi_mentions", label: "Signaux business" },
        { id: "actionable_recommendations", label: "Recommandations" },
        { id: "risks_ambiguities_and_data_quality_limits", label: "Limites et incertitudes" },
      ]
    : [
        { id: "executive_summary", label: "Executive Summary" },
        { id: "session_scores", label: "Session Scores" },
        { id: "key_moments", label: "Key Moments" },
        { id: "speaker_and_interaction_analysis", label: "Speaker Dynamics" },
        { id: "audience_intent_analysis", label: "Audience Intent" },
        { id: "cross_source_synthesis", label: "Cross-Source Synthesis" },
        { id: "friction_and_risk_signals", label: "Friction And Risks" },
        { id: "business_signals_and_kpi_mentions", label: "Business Signals" },
        { id: "actionable_recommendations", label: "Recommendations" },
        { id: "risks_ambiguities_and_data_quality_limits", label: "Limits And Uncertainty" },
      ]
);

function normalizeSectionHeading(value) {
  return String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/^\s*#+\s*/, "")
    .replace(/^\s*\d+[.)]\s*/, "")
    .replace(/^\s*[-*•]\s+/, "")
    .trim()
    .replace(/[:*_`-]+$/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

const deepHeadingAliases = computed(() => ({
  executive_summary: ["executive_summary", "resume_executif", "resume_executif_"],
  session_scores: ["session_scores", "scores_de_la_session", "scores_de_session"],
  key_moments: ["key_moments", "moments_cles", "moments_cle"],
  speaker_and_interaction_analysis: [
    "speaker_and_interaction_analysis",
    "speaker_dynamics",
    "dynamique_des_intervenants",
  ],
  audience_intent_analysis: ["audience_intent_analysis", "analyse_de_l_intention_du_public", "intentions_de_l_audience"],
  cross_source_synthesis: ["cross_source_synthesis", "synthese_inter_sources", "synthese_croisee"],
  friction_and_risk_signals: [
    "friction_and_risk_signals",
    "friction_and_risks",
    "signaux_de_friction_et_de_risque",
    "friction_et_risques",
  ],
  business_signals_and_kpi_mentions: [
    "business_signals_and_kpi_mentions",
    "business_signals",
    "signaux_d_affaires_et_mentions_de_kpi",
    "signaux_business",
  ],
  actionable_recommendations: ["actionable_recommendations", "recommandations_actionnables", "recommendations"],
  risks_ambiguities_and_data_quality_limits: [
    "risks_ambiguities_and_data_quality_limits",
    "limits_and_uncertainty",
    "risques_ambiguites_et_limites_de_qualite_des_donnees",
    "risques_ambiguities_et_limites_de_qualite_des_donnees",
    "limites_et_incertitudes",
  ],
}));

const parsedDeepSections = computed(() => {
  const text = String(deepBody.value || "").trim();
  if (!text) return {};

  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const sections = {};
  let currentKey = "executive_summary";
  let buffer = [];

  const flush = () => {
    const content = buffer.join("\n").trim();
    if (content) {
      sections[currentKey] = sections[currentKey] ? `${sections[currentKey]}\n\n${content}` : content;
    }
    buffer = [];
  };

  const tryMatchHeading = (line) => {
    const normalized = normalizeSectionHeading(line);
    return Object.entries(deepHeadingAliases.value).find(([, aliases]) => aliases.includes(normalized))?.[0] || "";
  };

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) {
      buffer.push(line);
      continue;
    }

    const matchedKey = tryMatchHeading(trimmed);
    if (matchedKey) {
      flush();
      currentKey = matchedKey;
      continue;
    }

    const colonIndex = trimmed.indexOf(":");
    if (colonIndex > 0) {
      const headingPart = trimmed.slice(0, colonIndex).trim();
      const inlineContent = trimmed.slice(colonIndex + 1).trim();
      const matchedInlineKey = tryMatchHeading(headingPart);
      if (matchedInlineKey) {
        flush();
        currentKey = matchedInlineKey;
        if (inlineContent) {
          buffer.push(inlineContent);
        }
        continue;
      }
    }
    buffer.push(line);
  }
  flush();
  return sections;
});

const activeDeepSectionBody = computed(() => parsedDeepSections.value?.[activeDeepSection.value] || "");
const deepTimelineRows = computed(() => state.workspace?.tables?.chatQuestionsTimeline || []);
const transcriptSegmentRows = computed(() => state.workspace?.tables?.transcriptSegments || []);
const deepReactionRows = computed(() => {
  const speakerNames = state.workspace?.speakerNames || {};
  const speakerByStartLabel = new Map(
    transcriptSegmentRows.value
      .map((row) => {
        const startLabel = String(row?.start_label || "").trim();
        const rawSpeaker = String(row?.speaker || "").trim();
        return [startLabel, speakerNames[rawSpeaker] || rawSpeaker || ""];
      })
      .filter(([startLabel, speaker]) => startLabel && speaker),
  );
  return (state.workspace?.tables?.chatQuestionReactionMoments || []).map((row) => {
    const rawSpeaker = String(row.speaker || "").trim();
    return {
      session_stage: row.session_stage,
      start_label: row.start_label,
      speaker:
        speakerNames[rawSpeaker] ||
        rawSpeaker ||
        speakerByStartLabel.get(String(row.start_label || "").trim()) ||
        "",
      excerpt: row.excerpt,
      chat_messages: row.chat_messages,
      question_count: row.question_count,
    };
  });
});

const reactionColumnLabels = computed(() => uiText.value.reactionColumns);

const reactionColumnWidths = {
  speaker: "12rem",
  session_stage: "12rem",
  start_label: "8rem",
  excerpt: "34rem",
  chat_messages: "10rem",
  question_count: "8rem",
};

function downloadBlob(filename, content, mimeType) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function downloadCsv(filename, rows) {
  if (!rows?.length) return;
  const columns = Object.keys(rows[0]);
  const escapeCell = (value) => {
    const text = String(value ?? "");
    if (/[",\n]/.test(text)) {
      return `"${text.replace(/"/g, '""')}"`;
    }
    return text;
  };
  const csv = [
    columns.join(","),
    ...rows.map((row) => columns.map((column) => escapeCell(row?.[column])).join(",")),
  ].join("\n");
  downloadBlob(filename, csv, "text/csv;charset=utf-8;");
}

async function runOverallFor(language) {
  activeOverallLanguage.value = language;
  await runAnalysis(language);
}

async function runDeepFor(language) {
  activeDeepLanguage.value = language;
  await runDeepAnalysis(language);
}
</script>

<template>
  <section class="page-section">
    <h2>{{ isFrenchUi ? "Analyse" : "Analysis" }}</h2>
    <p class="page-description">{{ pageDescription }}</p>

    <template v-if="state.workspace && hasTranscriptData">
      <div class="section-tabs analysis-language-tabs">
        <button
          v-for="tab in languageTabs"
          :key="tab.id"
          type="button"
          class="section-tab"
          :class="{ active: (activeCategory === 'overall' ? activeOverallLanguage : activeDeepLanguage) === tab.id }"
          @click="activeCategory === 'overall' ? (activeOverallLanguage = tab.id) : (activeDeepLanguage = tab.id)"
        >
          {{ tab.icon }} {{ tab.label }}
        </button>
      </div>

      <div class="section-tabs">
        <button
          v-for="category in categories"
          :key="category.id"
          type="button"
          class="section-tab"
          :class="{ active: activeCategory === category.id }"
          @click="activeCategory = category.id"
        >
          {{ category.label }}
        </button>
      </div>

      <template v-if="activeCategory === 'overall'">
        <div class="action-row" v-if="!hasOverallBody">
          <button class="primary" :disabled="state.loading.analysis" @click="runOverallFor(activeOverallLanguage)">
            {{ state.loading.analysis ? uiText.running : uiText.overallButton }}
          </button>
        </div>
        <div class="action-row" v-else>
          <a class="ghost-link-button" :href="overallPdfUrl">{{ uiText.downloadPdf }}</a>
        </div>
        <RichMarkdownCard :body="overallBody" :empty-message="uiText.empty" />
      </template>

      <template v-else>
        <div class="action-row" v-if="!hasDeepBody">
          <button class="primary" :disabled="state.loading.deepAnalysis" @click="runDeepFor(activeDeepLanguage)">
            {{ state.loading.deepAnalysis ? uiText.running : uiText.deepButton }}
          </button>
        </div>
        <div class="action-row" v-else>
          <a class="ghost-link-button" :href="deepPdfUrl">{{ uiText.downloadPdf }}</a>
        </div>
        <div class="section-tabs analysis-deep-section-tabs" v-if="hasDeepBody">
          <button
            v-for="section in deepSectionLabels"
            :key="section.id"
            type="button"
            class="section-tab"
            :class="{ active: activeDeepSection === section.id }"
            @click="activeDeepSection = section.id"
          >
            {{ section.label }}
          </button>
        </div>
        <RichMarkdownCard :body="activeDeepSectionBody" :empty-message="uiText.emptyDeep" />
        <template v-if="hasDeepBody && activeDeepSection === 'cross_source_synthesis'">
          <ContentPaceAudienceActivityChartCard
            v-if="deepTimelineRows.length"
            :title="uiText.contentPaceTitle"
            :is-french="isFrenchUi"
            :rows="deepTimelineRows"
          />

          <section class="panel" v-if="deepReactionRows.length">
            <div class="panel-heading panel-heading-inline table-toolbar-panel">
              <div class="panel-heading-inline-title">
                <h3>{{ uiText.reactionMomentsTitle }}</h3>
                <button
                  class="inline-icon-button"
                  type="button"
                  :title="isFrenchUi ? 'Telecharger le CSV des segments avec le plus de reactions' : 'Download segments with the most reactions CSV'"
                  @click="downloadCsv('segments-with-the-most-reactions.csv', deepReactionRows)"
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M12 3v10.2l3.6-3.6 1.4 1.4-6 6-6-6 1.4-1.4 3.6 3.6V3H12zm-7 14h14v2H5v-2z" fill="currentColor" />
                  </svg>
                </button>
              </div>
            </div>
            <DataTable
              :rows="deepReactionRows"
              :column-labels="reactionColumnLabels"
              :column-widths="reactionColumnWidths"
              csv-filename="segments-with-the-most-reactions.csv"
              :show-toolbar="false"
            />
          </section>
        </template>
      </template>
    </template>
    <section v-else-if="isTranscriptLoading" class="panel loading-panel">
      <h3>Transcript still loading</h3>
      <p>Analysis will become available as soon as the transcript finishes processing.</p>
    </section>
    <section v-else-if="isTranscriptUnavailable" class="panel helper-panel">
      <h3>Analysis unavailable for this session</h3>
      <p>{{ state.transcriptUnavailableReason }}</p>
    </section>
  </section>
</template>
