<script setup>
import { ref } from "vue";

// A GIF someone posted in the chat (`utils/gif.js` decides which links are
// GIFs). Loaded only when scrolled into view, with no referrer, at most
// 160px high. If it fails to load, the link shows as text instead.
defineProps({
  src: { type: String, required: true },
});

const failed = ref(false);
</script>

<template>
  <span v-if="failed" class="cg-link">{{ src }}</span>
  <img
    v-else
    class="cg"
    :src="src"
    alt="A GIF posted in the chat"
    loading="lazy"
    decoding="async"
    referrerpolicy="no-referrer"
    @error="failed = true"
  />
</template>

<style scoped>
.cg {
  display: block;
  max-width: min(100%, 280px);
  max-height: 160px;
  width: auto;
  height: auto;
  margin-top: var(--siq-space-1);
  border-radius: var(--siq-radius-inline);
  background: var(--siq-surface-2);
}

.cg-link {
  overflow-wrap: anywhere;
}

@media print {
  .cg {
    max-height: 80px;
  }
}
</style>
