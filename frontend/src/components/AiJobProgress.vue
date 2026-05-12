<script setup>
import { computed } from "vue";

// Renders a stage-floor progress bar for an in-flight AI job. Driven by
// the `aiJobs[kind]` slice in the workspace store. Consumers pass:
//   - job:   reactive job entry from the store (or null when no job active)
//   - flow:  one of 'overall_analysis' | 'deep_analysis' | 'smart_recap'
//            | 'content_repurposing' — controls the "analyzing" label
//   - isFrench (optional): switches the labels to French
//
// Phase 2 commit 3: functional but unstyled beyond the basics. Phase 4
// (UI polish) is the right time to bring this in line with the brand
// system — for now we just need the user to see "something is happening
// and it's progressing."

const props = defineProps({
  job: { type: Object, default: null },
  flow: { type: String, default: "" },
  isFrench: { type: Boolean, default: false },
});

const STAGE_LABELS_EN = {
  queued: "Queued",
  loading_sources: "Loading session data",
  building_prompt: "Preparing prompt",
  analyzing: {
    overall_analysis: "Generating overall analysis",
    deep_analysis: "Running deep analysis",
    smart_recap: "Generating recap",
    content_repurposing: "Generating content",
    _default: "Analyzing",
  },
  persisting: "Saving result",
  done: "Done",
};

const STAGE_LABELS_FR = {
  queued: "En file d'attente",
  loading_sources: "Chargement des données",
  building_prompt: "Préparation du prompt",
  analyzing: {
    overall_analysis: "Génération de l'analyse globale",
    deep_analysis: "Analyse approfondie en cours",
    smart_recap: "Génération du recap",
    content_repurposing: "Génération du contenu",
    _default: "Analyse en cours",
  },
  persisting: "Sauvegarde du résultat",
  done: "Terminé",
};

const stageLabel = computed(() => {
  const stage = String(props.job?.progress?.stage || props.job?.jobStatus || "queued").toLowerCase();
  const table = props.isFrench ? STAGE_LABELS_FR : STAGE_LABELS_EN;
  const entry = table[stage];
  if (!entry) return stage;
  if (typeof entry === "string") return entry;
  return entry[props.flow] || entry._default;
});

const percent = computed(() => {
  const value = Number(props.job?.progress?.percent);
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, Math.round(value)));
});

const label = computed(() => {
  // The worker can attach a more specific label via `extra.label` on
  // publish_progress. If present, prefer it over the generic stage name.
  const specific = String(props.job?.progress?.label || "").trim();
  return specific || stageLabel.value;
});
</script>

<template>
  <div v-if="job" class="ai-job-progress" role="status" aria-live="polite">
    <div class="ai-job-progress-header">
      <span class="ai-job-progress-label">{{ label }}</span>
      <span class="ai-job-progress-percent">{{ percent }}%</span>
    </div>
    <div class="ai-job-progress-track">
      <div class="ai-job-progress-fill" :style="{ width: percent + '%' }"></div>
    </div>
  </div>
</template>

<style scoped>
.ai-job-progress {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 8px 0 16px;
  font-size: 14px;
}
.ai-job-progress-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}
.ai-job-progress-label {
  color: var(--nimbus-700, #3f4950);
  font-weight: 500;
}
.ai-job-progress-percent {
  font-variant-numeric: tabular-nums;
  color: var(--nimbus-500, #5d6d79);
}
.ai-job-progress-track {
  height: 6px;
  background: var(--nimbus-100, #eaeef1);
  border-radius: 999px;
  overflow: hidden;
}
.ai-job-progress-fill {
  height: 100%;
  background: var(--brand-blue, #0b42c3);
  transition: width 600ms ease-out;
}
</style>
