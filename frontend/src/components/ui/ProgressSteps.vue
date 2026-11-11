<script setup>
import Icon from "./Icon.vue";

// One loader for every job a user waits on (a session being prepared, an
// analysis, a transcript, an AI output): the steps, each with a line of
// detail, a spinner on the current one; a bar that fills with the job's real
// progress (a moving sheen while the percent is unknown); and the time so far.
// `steps`: `[{label, state, detail}]`, state done | current | todo | skipped.
defineProps({
  steps: { type: Array, default: () => [] },
  // 0–100, or null while the job hasn't said how far it is.
  percent: { type: Number, default: null },
  // The bar's accessible name ("Preparing the report").
  label: { type: String, required: true },
  // "1:05", or "" before the clock starts.
  elapsed: { type: String, default: "" },
  note: { type: String, default: "It keeps going if you leave this page." },
});

const SPOKEN = { done: ": done", current: ": in progress", todo: ": next", skipped: ": skipped" };
</script>

<template>
  <div class="siq-progress">
    <ol v-if="steps.length" class="siq-progress-steps" aria-label="Steps">
      <li v-for="step in steps" :key="step.label" class="siq-progress-step" :class="`is-${step.state}`">
        <span class="siq-progress-mark" aria-hidden="true">
          <Icon v-if="step.state === 'done'" name="check" :size="14" />
          <span v-else-if="step.state === 'current'" class="siq-progress-spinner" />
        </span>
        <span class="siq-progress-text">
          <span class="siq-progress-label">{{ step.label }}</span>
          <span v-if="step.detail" class="siq-progress-detail">{{ step.detail }}</span>
        </span>
        <span class="siq-sr-only">{{ SPOKEN[step.state] || "" }}</span>
      </li>
    </ol>
    <div
      class="siq-progress-bar"
      :class="{ 'is-waiting': percent === null }"
      role="progressbar"
      :aria-label="label"
      :aria-valuenow="percent ?? undefined"
      aria-valuemin="0"
      aria-valuemax="100"
    >
      <span class="siq-progress-fill" :style="{ width: `${percent ?? 0}%` }" />
    </div>
    <p class="siq-progress-note">
      <span v-if="elapsed">Running for {{ elapsed }}. </span>{{ note }}
    </p>
  </div>
</template>

<style scoped>
.siq-progress-steps {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-3);
  margin: var(--siq-space-4) 0;
  padding: 0;
  list-style: none;
}

.siq-progress-step {
  display: flex;
  align-items: flex-start;
  gap: var(--siq-space-3);
  color: var(--siq-muted);
}

.siq-progress-step.is-done,
.siq-progress-step.is-current {
  color: var(--siq-ink);
}

.siq-progress-step.is-skipped .siq-progress-label {
  text-decoration: line-through;
}

.siq-progress-mark {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  margin-top: 2px;
  border-radius: 50%;
  box-shadow: inset 0 0 0 1.5px var(--siq-border);
}

.siq-progress-step.is-done .siq-progress-mark {
  background: var(--siq-accent);
  box-shadow: none;
  color: var(--siq-surface);
}

.siq-progress-step.is-current .siq-progress-mark {
  box-shadow: inset 0 0 0 1.5px var(--siq-accent);
}

.siq-progress-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.siq-progress-label {
  font-weight: var(--siq-weight-medium);
}

.siq-progress-detail,
.siq-progress-note {
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
}

.siq-progress-note {
  margin: var(--siq-space-2) 0 0;
}

.siq-progress-spinner {
  display: block;
  width: 10px;
  height: 10px;
  border: 2px solid var(--siq-accent);
  border-right-color: transparent;
  border-radius: 50%;
  animation: siq-progress-spin 0.9s linear infinite;
}

/* The bar: the job's progress, eased; a moving sheen says it's alive. */
.siq-progress-bar {
  position: relative;
  height: 6px;
  overflow: hidden;
  border-radius: 999px;
  background: var(--siq-surface-2);
}

.siq-progress-fill {
  position: absolute;
  inset: 0 auto 0 0;
  min-width: 6px;
  border-radius: inherit;
  background: var(--siq-accent);
  transition-property: width;
  transition-duration: 1.2s;
  transition-timing-function: var(--siq-ease);
}

.siq-progress-bar::after {
  content: "";
  position: absolute;
  inset: 0;
  width: 30%;
  background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--siq-surface) 45%, transparent), transparent);
  animation: siq-progress-sheen 1.8s linear infinite;
}

.siq-progress-bar.is-waiting .siq-progress-fill {
  width: 0 !important;
}

@keyframes siq-progress-spin {
  to {
    transform: rotate(360deg);
  }
}

@keyframes siq-progress-sheen {
  from {
    transform: translateX(-100%);
  }
  to {
    transform: translateX(340%);
  }
}

@media (prefers-reduced-motion: reduce) {
  .siq-progress-spinner,
  .siq-progress-bar::after {
    animation: none;
  }
  .siq-progress-fill {
    transition: none;
  }
}

@media (forced-colors: active) {
  .siq-progress-mark,
  .siq-progress-bar {
    border: 1px solid CanvasText;
  }
}
</style>
