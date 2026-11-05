<script setup>
import { ref, watch } from "vue";
import Icon from "./Icon.vue";
import IconTile from "./IconTile.vue";

// A section that opens and closes, built on real <details>/<summary>, so it
// works with the keyboard (Tab, then Enter or Space) and with find-in-page.
//
// variant "card": a collapsible card. The summary row is the card header
//   (icon tile, title, one-line takeaway, key-number chip, chevron).
// variant "link": a quiet "How we know" / "Evidence (3)" toggle inside a card.
//
// The chevron shows the state (down when closed, up when open); there is no
// rotation animation.
//
// The card title is a real heading inside <summary> (valid HTML). Some screen
// readers expose a summary as a button and flatten what is inside it, so the
// title may not show up in heading navigation there; the section is still
// announced by its title as a collapsed or expanded button. A page whose
// outline matters should put its own section heading above a group of these.
const props = defineProps({
  title: { type: String, required: true },
  icon: { type: String, default: "" },
  iconTone: { type: String, default: "default" },
  // One line, shown in the summary even when closed.
  takeaway: { type: String, default: "" },
  // A key number for the summary row, e.g. "11 open".
  chip: { type: String, default: "" },
  open: { type: Boolean, default: false },
  variant: {
    type: String,
    default: "card",
    validator: (value) => ["card", "link"].includes(value),
  },
  // Heading level of the title in the card variant.
  level: { type: Number, default: 2, validator: (value) => Number.isInteger(value) && value >= 1 && value <= 6 },
});

const emit = defineEmits(["update:open"]);
const isOpen = ref(props.open);

watch(
  () => props.open,
  (next) => {
    isOpen.value = next;
  },
);

function onToggle(event) {
  isOpen.value = event.target.open;
  emit("update:open", isOpen.value);
}
</script>

<template>
  <details class="siq-collapsible" :class="`siq-collapsible-${variant}`" :open="isOpen" @toggle="onToggle">
    <summary class="siq-summary siq-fx">
      <template v-if="variant === 'card'">
        <IconTile v-if="icon" :icon="icon" :tone="iconTone" />
        <span class="siq-summary-text">
          <component :is="`h${level}`" class="siq-summary-title">{{ title }}</component>
          <span v-if="takeaway" class="siq-summary-takeaway">{{ takeaway }}</span>
          <!-- On phones the chip moves under the takeaway (one copy is shown at a time). -->
          <span v-if="chip" class="siq-summary-chip siq-summary-chip-below">{{ chip }}</span>
        </span>
        <span v-if="chip" class="siq-summary-chip siq-summary-chip-end">{{ chip }}</span>
        <Icon :name="isOpen ? 'chevron-up' : 'chevron-down'" :size="18" class="siq-summary-chevron" />
      </template>
      <template v-else>
        <Icon v-if="icon" :name="icon" :size="14" />
        <span>{{ title }}</span>
        <Icon :name="isOpen ? 'chevron-up' : 'chevron-down'" :size="14" />
      </template>
    </summary>
    <div class="siq-collapsible-body">
      <slot />
    </div>
  </details>
</template>

<style scoped>
.siq-summary {
  list-style: none;
  cursor: pointer;
}

.siq-summary::-webkit-details-marker {
  display: none;
}

/* Card variant */
.siq-collapsible-card {
  background: var(--siq-card);
  border-radius: var(--siq-radius-card);
  box-shadow: var(--siq-card-shadow), inset 0 0 0 1px var(--siq-hairline);
  min-width: 0;
}

.siq-collapsible-card > .siq-summary {
  display: flex;
  align-items: center;
  gap: var(--siq-space-3);
  min-height: var(--siq-target);
  padding: var(--siq-space-5) var(--siq-space-6);
  border-radius: var(--siq-radius-card);
}

/* In light mode card-hover equals the page colour, so the hovered header
   redraws the card's hairline edge or it would melt into the page. */
.siq-collapsible-card > .siq-summary:hover {
  background: var(--siq-card-hover);
  box-shadow: inset 0 0 0 1px var(--siq-hairline);
}

.siq-collapsible-card[open] > .siq-summary:hover {
  box-shadow:
    inset 1px 0 0 var(--siq-hairline),
    inset -1px 0 0 var(--siq-hairline),
    inset 0 1px 0 var(--siq-hairline);
}

.siq-collapsible-card[open] > .siq-summary {
  border-bottom-left-radius: 0;
  border-bottom-right-radius: 0;
}

.siq-summary-text {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.siq-summary-title {
  margin: 0;
  font-size: var(--siq-text-card-title);
  line-height: var(--siq-text-card-title-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink);
  text-wrap: balance;
}

.siq-summary-takeaway {
  margin-top: 2px;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-ink-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.siq-summary-chip {
  flex: none;
  padding: 2px 10px;
  border-radius: var(--siq-radius-pill);
  background: var(--siq-surface-2);
  color: var(--siq-ink);
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  font-weight: var(--siq-weight-medium);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.siq-summary-chip-below {
  display: none;
}

.siq-summary-chevron {
  color: var(--siq-muted);
}

.siq-collapsible-card > .siq-collapsible-body {
  padding: var(--siq-space-4) var(--siq-space-6) var(--siq-space-6);
  box-shadow: inset 0 1px 0 var(--siq-hairline-row);
}

/* Link variant */
.siq-collapsible-link > .siq-summary {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: var(--siq-target);
  padding: 0 var(--siq-space-2);
  margin-left: calc(-1 * var(--siq-space-2));
  border-radius: var(--siq-radius-control);
  color: var(--siq-accent);
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  font-weight: var(--siq-weight-medium);
}

.siq-collapsible-link > .siq-summary:hover {
  color: var(--siq-accent-hover);
}

.siq-collapsible-link > .siq-collapsible-body {
  padding: var(--siq-space-2) 0 var(--siq-space-1);
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-reading-lh);
  color: var(--siq-ink-2);
}

/* Windows High Contrast drops box-shadows: draw the card edge with a border. */
@media (forced-colors: active) {
  .siq-collapsible-card {
    border: 1px solid CanvasText;
  }
}

@media (max-width: 640px) {
  .siq-collapsible-card > .siq-summary {
    padding: var(--siq-space-4);
  }

  /* Narrow screens: the takeaway gets two lines and the chip its own line
     under it, instead of truncating the takeaway to a few words. */
  .siq-summary-takeaway {
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    white-space: normal;
  }

  .siq-summary-chip-end {
    display: none;
  }

  .siq-summary-chip-below {
    display: inline-block;
    align-self: flex-start;
    margin-top: var(--siq-space-2);
  }

  .siq-collapsible-card > .siq-collapsible-body {
    padding: var(--siq-space-4);
  }
}
</style>
