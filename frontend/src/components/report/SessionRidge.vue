<script setup>
import { computed } from "vue";
import { busiestMinute, clockLabel, ridgePaths } from "../../report/chartGeometry";

// The ridge: the session's shape at the top of the Report, the audience's
// chat messages per live minute (`record.pulse.minutes`, lane `audienceChat`).
// The SVG stretches to the page's width; its stroke keeps 1px. It has no
// axis: the Pulse below it carries one. Spoken as one sentence.

const props = defineProps({
  // `record.pulse.minutes`: one row per live minute, zero-filled.
  minutes: { type: Array, required: true },
});

const values = computed(() => props.minutes.map((row) => row?.audienceChat ?? 0));
const paths = computed(() => ridgePaths(values.value));

const description = computed(() => {
  const count = props.minutes.length;
  const peak = busiestMinute(props.minutes, "audienceChat");
  const shape = `The session's shape: audience chat messages per minute over ${count} ${count === 1 ? "minute" : "minutes"}`;
  if (!peak) return `${shape}. Nobody in the audience wrote in the chat during the session.`;
  const messages = `${peak.count} ${peak.count === 1 ? "message" : "messages"}`;
  return `${shape}, busiest at ${clockLabel(peak.minute * 60)} with ${messages}.`;
});
</script>

<template>
  <div v-if="minutes.length" class="r-ridge" role="img" :aria-label="description">
    <svg viewBox="0 0 1000 100" preserveAspectRatio="none" aria-hidden="true" focusable="false">
      <path class="r-ridge-area" :d="paths.area" />
      <path class="r-ridge-line" :d="paths.line" />
    </svg>
  </div>
</template>

<style scoped>
.r-ridge {
  position: relative;
  height: 104px;
}

.r-ridge svg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  display: block;
}

.r-ridge-area {
  fill: var(--siq-ridge-fill);
}

.r-ridge-line {
  fill: none;
  stroke: var(--siq-ink-2);
  stroke-width: 1px;
  vector-effect: non-scaling-stroke;
}

@media (max-width: 640px) {
  .r-ridge {
    height: 64px;
  }
}

@media (forced-colors: active) {
  .r-ridge-line {
    stroke: CanvasText;
  }
}
</style>
