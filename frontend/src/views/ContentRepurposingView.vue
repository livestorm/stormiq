<script setup>
import { computed, ref } from "vue";
import RichMarkdownCard from "../components/RichMarkdownCard.vue";
import { api } from "../api";
import { useWorkspace } from "../store/workspace";

const { state, runContentRepurposing, hasTranscriptData, isTranscriptLoading, isTranscriptUnavailable } = useWorkspace();

const activeLanguage = ref("English");
const activeContentType = ref("summary");

const languageTabs = [
  { id: "English", label: "English", icon: "🇬🇧" },
  { id: "French", label: "Français", icon: "🇫🇷" },
];

const contentBundle = computed(() => state.workspace?.outputs?.contentRepurposeBundle || {});
const activeLanguageBundle = computed(() => contentBundle.value?.[activeLanguage.value] || {});
const activeBody = computed(() => String(activeLanguageBundle.value?.[activeContentType.value] || "").trim());
const hasActiveBody = computed(() => Boolean(activeBody.value));
const hasActiveLanguageContent = computed(() =>
  Object.values(activeLanguageBundle.value || {}).some((value) => String(value || "").trim())
);
const activePdfUrl = computed(() =>
  state.workspace?.sessionId
    ? api.getContentRepurposingPdfUrl(state.workspace.sessionId, activeLanguage.value, activeContentType.value)
    : "#"
);

const isFrenchUi = computed(() => activeLanguage.value === "French");

const uiText = computed(() =>
  isFrenchUi.value
    ? {
        title: "Réutilisation de contenu",
        description: "Transformez la session en résumé, article, email et contenu social.",
        tabs: [
          { id: "summary", label: "Résumé" },
          { id: "blog", label: "Article de blog" },
          { id: "email", label: "Email de suivi" },
          { id: "social_media", label: "Posts réseaux sociaux" },
        ],
        generate: "Générer le contenu",
        generating: "Génération...",
        downloadPdf: "Télécharger le PDF",
        empty: "Aucun contenu disponible pour cette section.",
        transcriptLoadingTitle: "Transcription en cours de chargement",
        transcriptLoadingBody: "La réutilisation de contenu sera disponible une fois la transcription prête.",
        transcriptUnavailableTitle: "Réutilisation de contenu indisponible pour cette session",
      }
    : {
        title: "Content Repurposing",
        description: "Turn the session into summary, blog, email, and social content.",
        tabs: [
          { id: "summary", label: "Summary" },
          { id: "blog", label: "Blog Post" },
          { id: "email", label: "Email Follow-up" },
          { id: "social_media", label: "Social Media Posts" },
        ],
        generate: "Generate Content",
        generating: "Generating...",
        downloadPdf: "Download PDF",
        empty: "No content available yet for this section.",
        transcriptLoadingTitle: "Transcript still loading",
        transcriptLoadingBody: "Content Repurposing will become available once the transcript is ready.",
        transcriptUnavailableTitle: "Content Repurposing unavailable for this session",
      }
);

const languageHint = computed(() =>
  activeLanguage.value === "English"
    ? "Content has already been generated for English. Switch language to generate the other version."
    : "Le contenu est deja genere en anglais. Passez en francais pour generer cette version."
);

async function generateForLanguage(language) {
  activeLanguage.value = language;
  await runContentRepurposing(language);
}
</script>

<template>
  <section class="page-section">
    <h2>{{ uiText.title }}</h2>
    <p class="page-description">{{ uiText.description }}</p>

    <template v-if="state.workspace && hasTranscriptData">
      <p class="analysis-subcopy" v-if="contentBundle?.English">{{ languageHint }}</p>

      <div class="section-tabs analysis-language-tabs">
        <button
          v-for="tab in languageTabs"
          :key="tab.id"
          type="button"
          class="section-tab"
          :class="{ active: activeLanguage === tab.id }"
          @click="activeLanguage = tab.id"
        >
          {{ tab.icon }} {{ tab.label }}
        </button>
      </div>

      <div class="section-tabs">
        <button
          v-for="tab in uiText.tabs"
          :key="tab.id"
          type="button"
          class="section-tab"
          :class="{ active: activeContentType === tab.id }"
          @click="activeContentType = tab.id"
        >
          {{ tab.label }}
        </button>
      </div>

      <div class="action-row" v-if="!hasActiveLanguageContent">
        <button class="primary" :disabled="state.loading.contentRepurposing" @click="generateForLanguage(activeLanguage)">
          {{ state.loading.contentRepurposing ? uiText.generating : uiText.generate }}
        </button>
      </div>

      <div class="action-row" v-else-if="hasActiveBody">
        <a class="ghost-link-button" :href="activePdfUrl">{{ uiText.downloadPdf }}</a>
      </div>

      <RichMarkdownCard :body="activeBody" :empty-message="uiText.empty" />
    </template>
    <section v-else-if="isTranscriptLoading" class="panel loading-panel">
      <h3>{{ uiText.transcriptLoadingTitle }}</h3>
      <p>{{ uiText.transcriptLoadingBody }}</p>
    </section>
    <section v-else-if="isTranscriptUnavailable" class="panel helper-panel">
      <h3>{{ uiText.transcriptUnavailableTitle || "Content Repurposing unavailable for this session" }}</h3>
      <p>{{ state.transcriptUnavailableReason }}</p>
    </section>
  </section>
</template>
