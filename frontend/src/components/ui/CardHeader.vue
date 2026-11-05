<script setup>
import IconTile from "./IconTile.vue";

// Card header: icon tile + title (+ optional one-line subtitle), with a right
// slot for a status word, a count badge or actions.
defineProps({
  icon: { type: String, default: "" },
  iconTone: { type: String, default: "default" },
  title: { type: String, required: true },
  subtitle: { type: String, default: "" },
  // Heading level for the title, so the page outline stays right.
  level: { type: Number, default: 2, validator: (value) => Number.isInteger(value) && value >= 1 && value <= 6 },
});
</script>

<template>
  <header class="siq-card-header">
    <IconTile v-if="icon" :icon="icon" :tone="iconTone" />
    <div class="siq-card-header-text">
      <component :is="`h${level}`" class="siq-card-title">{{ title }}</component>
      <p v-if="subtitle" class="siq-card-subtitle">{{ subtitle }}</p>
    </div>
    <div v-if="$slots.right" class="siq-card-header-right">
      <slot name="right" />
    </div>
  </header>
</template>

<style scoped>
.siq-card-header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--siq-space-2) var(--siq-space-3);
  min-height: var(--siq-target);
}

.siq-card-header-text {
  flex: 1 1 200px;
  min-width: 0;
}

.siq-card-title {
  margin: 0;
  font-family: var(--siq-font);
  font-size: var(--siq-text-card-title);
  line-height: var(--siq-text-card-title-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink);
  text-wrap: balance;
}

.siq-card-subtitle {
  margin: 2px 0 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.siq-card-header-right {
  flex: none;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--siq-space-2);
  margin-left: auto;
}

@media (max-width: 640px) {
  /* Two lines on phones rather than a subtitle cut to a few words. */
  .siq-card-subtitle {
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    white-space: normal;
  }
}
</style>
