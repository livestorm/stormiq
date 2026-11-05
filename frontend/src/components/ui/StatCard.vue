<script setup>
import { computed, ref, useId } from "vue";
import Icon from "./Icon.vue";
import IconTile from "./IconTile.vue";
import StatusWord from "./StatusWord.vue";

// A stat card: icon tile, label, one big tabular number, a one-line sub-line,
// and an optional info button that opens how the number is computed.
const props = defineProps({
  icon: { type: String, required: true },
  label: { type: String, required: true },
  value: { type: [String, Number], required: true },
  // Small text right after the number ("of 45 min").
  unit: { type: String, default: "" },
  sub: { type: String, default: "" },
  // The definition shown by the info button; no button without it.
  info: { type: String, default: "" },
  // Status word shown with the definition (measured / estimated / check).
  status: { type: String, default: "" },
  infoOpen: { type: Boolean, default: false },
  // Heading level of the label, so the page outline stays right.
  level: { type: Number, default: 3, validator: (value) => Number.isInteger(value) && value >= 1 && value <= 6 },
});

const open = ref(props.infoOpen);
const popoverId = `siq-stat-info-${useId()}`;
const infoLabel = computed(() => `How is ${props.label} computed?`);

function close(event) {
  if (!open.value) return;
  open.value = false;
  event?.currentTarget?.querySelector?.("button")?.focus();
}
</script>

<template>
  <article class="siq-stat" @keydown.esc="close">
    <div class="siq-stat-head">
      <IconTile :icon="icon" />
      <component :is="`h${level}`" class="siq-stat-label">{{ label }}</component>
      <button
        v-if="info"
        type="button"
        class="siq-stat-info siq-fx"
        :aria-label="infoLabel"
        :aria-expanded="String(open)"
        :aria-controls="popoverId"
        @click="open = !open"
      >
        <Icon name="info" :size="16" />
      </button>
    </div>
    <p class="siq-stat-value">
      {{ value }}<span v-if="unit" class="siq-stat-unit">{{ unit }}</span>
    </p>
    <p v-if="sub" class="siq-stat-sub">{{ sub }}</p>
    <div v-if="info" v-show="open" :id="popoverId" class="siq-stat-popover" role="region" :aria-label="infoLabel">
      <p class="siq-stat-popover-text">{{ info }}</p>
      <StatusWord v-if="status" :status="status" />
    </div>
  </article>
</template>

<style scoped>
.siq-stat {
  min-width: 0;
  display: flex;
  flex-direction: column;
  padding: var(--siq-space-6);
  background: var(--siq-card);
  border-radius: var(--siq-radius-card);
  box-shadow: var(--siq-card-shadow), inset 0 0 0 1px var(--siq-hairline);
}

.siq-stat-head {
  display: flex;
  align-items: center;
  gap: var(--siq-space-3);
  min-height: var(--siq-target);
}

.siq-stat-label {
  flex: 1 1 auto;
  min-width: 0;
  margin: 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-muted);
}

.siq-stat-info {
  flex: none;
  width: var(--siq-target);
  height: var(--siq-target);
  margin: -4px -8px -4px 0;
  padding: 0;
  border: 0;
  border-radius: var(--siq-radius-pill);
  background: transparent;
  color: var(--siq-muted);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}

.siq-stat-info:hover,
.siq-stat-info[aria-expanded="true"] {
  color: var(--siq-selected-ink);
  background: var(--siq-selected-bg);
}

.siq-stat-value {
  margin: var(--siq-space-3) 0 0;
  font-size: var(--siq-text-display);
  line-height: var(--siq-text-display-lh);
  font-weight: var(--siq-weight-regular);
  color: var(--siq-ink);
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.01em;
}

.siq-stat-unit {
  margin-left: 6px;
  font-size: var(--siq-text-lead);
  line-height: var(--siq-text-lead-lh);
  color: var(--siq-ink-2);
  letter-spacing: 0;
}

.siq-stat-sub {
  margin: var(--siq-space-1) 0 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.siq-stat-popover {
  margin-top: var(--siq-space-3);
  padding: var(--siq-space-3) var(--siq-space-4);
  border-radius: var(--siq-radius-control);
  background: var(--siq-surface);
  box-shadow: inset 0 0 0 1px var(--siq-hairline-head);
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--siq-space-2);
}

.siq-stat-popover-text {
  margin: 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-ink-2);
}

/* Windows High Contrast drops box-shadows: draw the edges with borders. */
@media (forced-colors: active) {
  .siq-stat,
  .siq-stat-popover {
    border: 1px solid CanvasText;
  }
}

@media (max-width: 640px) {
  .siq-stat {
    padding: var(--siq-space-4);
  }

  /* Two lines on phones rather than a sub-line cut to a few words. */
  .siq-stat-sub {
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    white-space: normal;
  }
}
</style>
