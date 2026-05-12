<script setup>
import { computed, onMounted, ref, watch } from "vue";
import AiJobProgress from "../components/AiJobProgress.vue";
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
const activeButtonLabel = "Generate";

// Auto-generate the Professional recap when missing.
//
// The worker auto-enqueues a Professional recap after every successful
// transcription, and the cached-workspace endpoint also catches up
// missed cases. But neither populates state.aiJobs.smart_recap on the
// frontend — so when the user lands here with an in-flight backend
// job they'd otherwise see the static "Generate" button while the bar
// is silent. Calling runSmartRecap('professional') here either hits
// the backend dedupe and attaches to the running job, or starts a
// fresh one. Polling kicks in either way and AiJobProgress takes over.
//
// Hype and Surprise stay opt-in — only Professional is the default.
function maybeAutoGenerateProfessional() {
  if (activeTone.value !== "professional") return;
  if (hasActiveBody.value) return;
  if (state.loading.smartRecap) return;
  if (!state.workspace?.sessionId) return;
  if (!hasTranscriptData.value) return;
  runSmartRecap("professional").catch(() => {
    // Errors surface via state.error.
  });
}

onMounted(maybeAutoGenerateProfessional);
watch(
  () => [state.workspace?.sessionId, activeTone.value, hasActiveBody.value, hasTranscriptData.value],
  () => maybeAutoGenerateProfessional(),
);
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

      <div class="action-row" v-if="!hasActiveBody">
        <button class="primary" :disabled="state.loading.smartRecap" @click="runSmartRecap(activeTone)">
          {{ state.loading.smartRecap ? "Generating..." : activeButtonLabel }}
        </button>
      </div>

      <div class="action-row" v-else>
        <a class="ghost-link-button" :href="activePdfUrl">Download PDF</a>
      </div>

      <AiJobProgress :job="state.aiJobs.smart_recap" flow="smart_recap" />

      <div class="smart-recap-result-shell">
        <div v-if="activeTone === 'surprise'" class="smart-recap-result-art">
          <img :src="'/brand-assets/icons/gc.png'" alt="Surprise Me artwork" class="smart-recap-result-art-image" />
        </div>
        <RichMarkdownCard :body="displayBody" :empty-message="activeDescription" />
      </div>
    </template>
    <section v-else-if="isTranscriptLoading" class="panel loading-panel">
      <h3>Transcript still loading</h3>
      <p>Smart Recap will become available once the transcript is ready.</p>
      <AiJobProgress :job="state.aiJobs.transcript" flow="transcript" />
    </section>
    <section v-else-if="isTranscriptUnavailable" class="panel helper-panel">
      <h3>Smart Recap unavailable for this session</h3>
      <p>{{ state.transcriptUnavailableReason }}</p>
    </section>
  </section>
</template>
