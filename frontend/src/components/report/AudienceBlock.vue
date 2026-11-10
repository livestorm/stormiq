<script setup>
import Collapsible from "../ui/Collapsible.vue";
import Icon from "../ui/Icon.vue";
import StatusWord from "../ui/StatusWord.vue";

// One part of a Report section: an icon and a title, its content, then a
// "How we know" toggle with the definition and the status word. Parts sit
// inside one card, separated by a hairline (never a card in a card).
defineProps({
  icon: { type: String, required: true },
  title: { type: String, required: true },
  // "measured", "estimated", or "" when the measure is hidden.
  status: { type: String, default: "" },
  info: { type: String, default: "" },
});
</script>

<template>
  <section class="r-block">
    <h3 class="r-block-title">
      <Icon :name="icon" :size="18" />
      <span>{{ title }}</span>
    </h3>
    <slot />
    <Collapsible v-if="info" variant="link" title="How we know">
      <p class="r-block-info">{{ info }}</p>
      <StatusWord v-if="status" :status="status" />
    </Collapsible>
  </section>
</template>

<style scoped>
.r-block {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-3);
  min-width: 0;
  padding: var(--siq-space-5) 0;
  box-shadow: inset 0 -1px 0 var(--siq-hairline-row);
}

.r-block:first-child {
  padding-top: var(--siq-space-2);
}

.r-block:last-child {
  padding-bottom: 0;
  box-shadow: none;
}

.r-block-title {
  display: flex;
  align-items: center;
  gap: var(--siq-space-2);
  margin: 0;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink);
}

.r-block-title :deep(svg) {
  color: var(--siq-ink-2);
}

.r-block-info {
  margin: 0 0 var(--siq-space-2);
  max-width: 680px;
}

@media (forced-colors: active) {
  .r-block {
    border-bottom: 1px solid CanvasText;
  }
  .r-block:last-child {
    border-bottom: none;
  }
}
</style>
