<script setup>
// The StormIQ mark: a circle with three ascending bars, plus the "Storm" + "IQ"
// wordmark. StormIQ's own identity, never the Livestorm logo (docs/brand.md).
// The same geometry is drawn in pdf.py (PDF headers) and public/favicon.svg.
defineProps({
  size: { type: Number, default: 28 },
  wordmark: { type: Boolean, default: true },
});
</script>

<template>
  <span class="siq-mark">
    <svg :width="size" :height="size" viewBox="0 0 28 28" aria-hidden="true" focusable="false">
      <circle cx="14" cy="14" r="14" class="siq-mark-disc" />
      <rect x="7.5" y="14" width="3" height="7" rx="1.5" class="siq-mark-bar" />
      <rect x="12.5" y="10.5" width="3" height="10.5" rx="1.5" class="siq-mark-bar" />
      <rect x="17.5" y="7" width="3" height="14" rx="1.5" class="siq-mark-bar" />
    </svg>
    <span v-if="wordmark" class="siq-mark-word">Storm<span class="siq-mark-iq">IQ</span></span>
  </span>
</template>

<style scoped>
.siq-mark {
  display: inline-flex;
  align-items: center;
  gap: 10px;
}

.siq-mark svg {
  flex: none;
  display: block;
}

.siq-mark-disc {
  fill: var(--siq-mark-fill);
}

.siq-mark-bar {
  fill: var(--siq-mark-bars);
}

.siq-mark-word {
  font-family: var(--siq-font);
  font-size: var(--siq-text-card-title);
  line-height: var(--siq-text-card-title-lh);
  font-weight: var(--siq-weight-semibold);
  letter-spacing: -0.01em;
  color: inherit;
}

.siq-mark-iq {
  color: var(--siq-mark-iq);
}
</style>
