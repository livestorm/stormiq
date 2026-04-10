<script setup>
import { computed, ref } from "vue";
import RichMarkdownCard from "../components/RichMarkdownCard.vue";
import { api } from "../api";
import { useWorkspace } from "../store/workspace";

const { state, runSmartRecap, hasTranscriptData, isTranscriptLoading, isTranscriptUnavailable } = useWorkspace();
const activeTone = ref("professional");

const recapTabs = [
  { id: "professional", label: "Professional" },
  { id: "hype", label: "Hype" },
  { id: "surprise", label: "Surprise Me!" },
];

const recapBundle = computed(() => state.workspace?.outputs?.smartRecapBundle || {});
const activeBody = computed(() => String(recapBundle.value?.[activeTone.value] || "").trim());
const hasActiveBody = computed(() => Boolean(activeBody.value));
const displayBody = computed(() => {
  const source = String(activeBody.value || "").trim();
  if (!source) return "";

  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const output = [];

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index].trim();

    if (/^#\s*title\s*$/i.test(line) || /^##\s*title\s*$/i.test(line)) {
      const nextLine = String(lines[index + 1] || "").trim();
      if (nextLine) {
        output.push(`## ${nextLine}`);
        index += 1;
      }
      continue;
    }

    if (/^#\s*description\s*$/i.test(line) || /^##\s*description\s*$/i.test(line)) {
      continue;
    }

    output.push(lines[index]);
  }

  return output.join("\n").trim();
});
const activePdfUrl = computed(() =>
  state.workspace?.sessionId ? api.getSmartRecapPdfUrl(state.workspace.sessionId, activeTone.value) : "#"
);

const recapDescriptions = {
  professional: "A polished recap for internal sharing or stakeholder readouts.",
  hype: "A more energetic version that keeps momentum and punch.",
  surprise: "A more unexpected angle that still stays grounded in the transcript.",
};

const activeDescription = computed(() => recapDescriptions[activeTone.value] || "");
const activeButtonLabel = computed(() => {
  if (activeTone.value === "professional") return "Generate Professional Recap";
  if (activeTone.value === "hype") return "Generate Hype Recap";
  return "Generate Surprise Me! Recap";
});
</script>

<template>
  <section class="page-section">
    <h2>Smart Recap</h2>
    <p class="page-description">Generate a lighter recap in professional, hype, or surprise mode.</p>

    <template v-if="state.workspace && hasTranscriptData">
      <div class="section-tabs">
        <button
          v-for="tab in recapTabs"
          :key="tab.id"
          type="button"
          class="section-tab"
          :class="{ active: activeTone === tab.id }"
          @click="activeTone = tab.id"
        >
          {{ tab.label }}
        </button>
      </div>

      <p class="analysis-subcopy">{{ activeDescription }}</p>

      <div class="action-row" v-if="!hasActiveBody">
        <button class="primary" :disabled="state.loading.smartRecap" @click="runSmartRecap(activeTone)">
          {{ state.loading.smartRecap ? "Generating..." : activeButtonLabel }}
        </button>
      </div>

      <div class="action-row" v-else>
        <a class="ghost-link-button" :href="activePdfUrl">Download PDF</a>
      </div>

      <div class="smart-recap-result-shell">
        <div v-if="activeTone === 'surprise'" class="smart-recap-result-art">
          <img :src="'/brand-assets/icons/gc.png'" alt="Surprise Me artwork" class="smart-recap-result-art-image" />
        </div>
        <RichMarkdownCard :body="displayBody" empty-message="No recap available yet." />
      </div>
    </template>
    <section v-else-if="isTranscriptLoading" class="panel loading-panel">
      <h3>Transcript still loading</h3>
      <p>Smart Recap will become available once the transcript is ready.</p>
    </section>
    <section v-else-if="isTranscriptUnavailable" class="panel helper-panel">
      <h3>Smart Recap unavailable for this session</h3>
      <p>{{ state.transcriptUnavailableReason }}</p>
    </section>
  </section>
</template>
