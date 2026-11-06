<script setup>
// The StormIQ mark: an "IQ" monogram in a circle, plus the "Storm" + "IQ"
// wordmark. StormIQ's own identity, never the Livestorm logo (docs/brand.md).
// The monogram fills the square spots (collapsed sidebar, compact top bar),
// and the same geometry is public/favicon.svg. Wherever there is room (top
// bar, classic sidebar, PDFs) the wordmark stands alone (`:mark="false"`),
// followed by the "for Livestorm" lockup (ForLivestorm.vue).
defineProps({
  size: { type: Number, default: 28 },
  wordmark: { type: Boolean, default: true },
  mark: { type: Boolean, default: true },
});
</script>

<template>
  <span class="siq-mark">
    <svg v-if="mark" :width="size" :height="size" viewBox="0 0 28 28" aria-hidden="true" focusable="false">
      <circle cx="14" cy="14" r="14" class="siq-mark-disc" />
      <rect x="6.6" y="8.2" width="3.4" height="11.6" rx="1.7" class="siq-mark-letter" />
      <circle cx="17.6" cy="13.6" r="4.4" class="siq-mark-stroke" />
      <path d="M19.9 16.4 22.4 19.4" class="siq-mark-stroke" />
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

.siq-mark-letter {
  fill: var(--siq-mark-ink);
}

.siq-mark-stroke {
  fill: none;
  stroke: var(--siq-mark-ink);
  stroke-width: 3.2;
  stroke-linecap: round;
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
