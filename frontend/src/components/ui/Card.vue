<script setup>
// A content card: the v1 design's main surface (docs/brand.md, "Cards").
// Never put a card inside a card: inside one, separate parts with
// <hr class="siq-card-rule"> or a hairline and space.
defineProps({
  // The element to render: "section" (default), "article", "div", "li"…
  as: { type: String, default: "section" },
  // "default" 24px, "hero" 32px (the page's lead card), "flush" none (tables).
  padding: {
    type: String,
    default: "default",
    validator: (value) => ["default", "hero", "flush"].includes(value),
  },
  // "fair" for a card that asks for the user's check (fair-tint background).
  tone: {
    type: String,
    default: "default",
    validator: (value) => ["default", "fair"].includes(value),
  },
});
</script>

<template>
  <component :is="as" class="siq-card" :class="[`siq-card-pad-${padding}`, `siq-card-tone-${tone}`]">
    <slot />
  </component>
</template>

<style scoped>
.siq-card {
  min-width: 0;
  margin: 0;
  background: var(--siq-card);
  color: var(--siq-ink);
  border-radius: var(--siq-radius-card);
  box-shadow: var(--siq-card-shadow), inset 0 0 0 1px var(--siq-hairline);
}

.siq-card-pad-default {
  padding: var(--siq-space-6);
}

.siq-card-pad-hero {
  padding: var(--siq-space-8);
}

.siq-card-pad-flush {
  padding: 0;
  overflow: hidden;
}

/* A flush card clips at its edge (rounded table corners), which would clip
   the focus ring of a control at the edge: draw rings inside instead. */
.siq-card-pad-flush :deep(.siq-fx:focus-visible) {
  outline-offset: -3px;
}

.siq-card-tone-fair {
  background: var(--siq-fair-tint);
}

/* Inside a fair card, fair-tinted parts (the icon tile, the "Needs your
   check" pill) sit on the card colour, or they would vanish into the card. */
.siq-card-tone-fair :deep(*) {
  --siq-fair-tint: var(--siq-card);
}

/* Windows High Contrast drops box-shadows: draw the edge with a border. */
@media (forced-colors: active) {
  .siq-card {
    border: 1px solid CanvasText;
  }
}

@media (max-width: 640px) {
  .siq-card-pad-default,
  .siq-card-pad-hero {
    padding: var(--siq-space-4);
  }
}
</style>
