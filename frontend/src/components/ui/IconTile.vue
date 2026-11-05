<script setup>
import Icon from "./Icon.vue";

// The 36×36 icon tile that leads every card header and stat card.
defineProps({
  icon: { type: String, required: true },
  // "default" (selected blue), "fair" (needs your check), "good", "low".
  tone: { type: String, default: "default" },
});
</script>

<template>
  <span class="siq-tile" :class="`siq-tile-${tone}`" aria-hidden="true">
    <Icon :name="icon" :size="18" />
  </span>
</template>

<style scoped>
.siq-tile {
  flex: none;
  width: 36px;
  height: 36px;
  border-radius: var(--siq-radius-tile);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--siq-selected-bg);
  color: var(--siq-selected-ink);
}

.siq-tile-fair {
  background: var(--siq-fair-tint);
  color: var(--siq-fair);
}

.siq-tile-good {
  background: var(--siq-good-tint);
  color: var(--siq-good);
}

.siq-tile-low {
  background: var(--siq-low-tint);
  color: var(--siq-low);
}
</style>
