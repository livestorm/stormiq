<script setup>
import { computed } from "vue";
import { RouterLink } from "vue-router";
import Icon from "./Icon.vue";

// Buttons of the v1 design (named UiButton because "Button" is a native
// element name). Renders a <button>, a router link (`to`) or a plain link
// (`href`). Variants: primary (one per view: the main action), secondary,
// ghost (quiet, accent text) and icon (40×40, needs `label`).
//
// A disabled link renders as an <a> without href and with
// aria-disabled="true", so it can't be followed or focused.
const props = defineProps({
  variant: {
    type: String,
    default: "secondary",
    validator: (value) => ["primary", "secondary", "ghost", "icon"].includes(value),
  },
  icon: { type: String, default: "" },
  iconRight: { type: String, default: "" },
  // Accessible name for icon-only buttons (required with variant="icon").
  label: { type: String, default: "" },
  to: { type: [String, Object], default: null },
  href: { type: String, default: "" },
  type: { type: String, default: "button" },
  disabled: { type: Boolean, default: false },
  small: { type: Boolean, default: false },
  // Glyph size and stroke; defaults are 18px for icon buttons, 16px otherwise.
  iconSize: { type: Number, default: 0 },
  iconStroke: { type: Number, default: 2 },
});

if (import.meta.env.DEV && props.variant === "icon" && !props.label) {
  console.warn('UiButton: variant="icon" needs a `label` (its accessible name).');
}

// Runs before any @click the parent passed (Vue calls this component's own
// handler first and skips the rest after stopImmediatePropagation).
function blockClick(event) {
  event.preventDefault();
  event.stopImmediatePropagation();
}

const isLink = computed(() => Boolean(props.to || props.href));

const tag = computed(() => {
  if (isLink.value && props.disabled) return "a";
  if (props.to) return RouterLink;
  return props.href ? "a" : "button";
});

const attrs = computed(() => {
  if (isLink.value && props.disabled) return { role: "link", "aria-disabled": "true", onClick: blockClick };
  if (props.to) return { to: props.to };
  if (props.href) return { href: props.href };
  return { type: props.type, disabled: props.disabled };
});

const glyphSize = computed(() => props.iconSize || (props.variant === "icon" ? 18 : 16));
</script>

<template>
  <component
    :is="tag"
    v-bind="attrs"
    class="siq-btn siq-fx"
    :class="[`siq-btn-${variant}`, { 'siq-btn-small': small }]"
    :aria-label="label || undefined"
    :title="variant === 'icon' && label ? label : undefined"
  >
    <Icon v-if="icon" :name="icon" :size="glyphSize" :stroke-width="iconStroke" />
    <span v-if="variant !== 'icon'" class="siq-btn-text"><slot /></span>
    <Icon v-if="iconRight && variant !== 'icon'" :name="iconRight" :size="glyphSize" :stroke-width="iconStroke" />
  </component>
</template>

<style scoped>
.siq-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--siq-space-2);
  min-height: var(--siq-target);
  padding: 0 var(--siq-space-4);
  border: 1px solid transparent;
  border-radius: var(--siq-radius-control);
  background: transparent;
  color: var(--siq-ink);
  font-family: var(--siq-font);
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  font-weight: var(--siq-weight-medium);
  text-decoration: none;
  white-space: nowrap;
  cursor: pointer;
}

.siq-btn:disabled,
.siq-btn[aria-disabled="true"] {
  cursor: not-allowed;
  opacity: 0.5;
}

.siq-btn-primary {
  background: var(--siq-cta);
  color: var(--siq-cta-ink);
}

.siq-btn-primary:hover:not(:disabled):not([aria-disabled="true"]) {
  background: var(--siq-cta-hover);
}

.siq-btn-secondary {
  border-color: var(--siq-border);
  color: var(--siq-ink);
}

.siq-btn-secondary:hover:not(:disabled):not([aria-disabled="true"]) {
  border-color: var(--siq-border-hover);
}

.siq-btn-ghost {
  padding: 0 var(--siq-space-3);
  color: var(--siq-accent);
}

.siq-btn-ghost:hover:not(:disabled):not([aria-disabled="true"]) {
  color: var(--siq-accent-hover);
}

.siq-btn-icon {
  width: var(--siq-target);
  padding: 0;
  color: var(--siq-ink-2);
}

.siq-btn-icon:hover:not(:disabled):not([aria-disabled="true"]) {
  color: var(--siq-ink);
  background: var(--siq-surface-2);
}

/* Smaller text for dense rows; the hit area stays 40px tall. */
.siq-btn-small {
  font-size: var(--siq-text-small);
  padding: 0 var(--siq-space-3);
}

.siq-btn-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
