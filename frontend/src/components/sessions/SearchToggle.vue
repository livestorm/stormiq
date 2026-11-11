<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from "vue";
import Icon from "../ui/Icon.vue";

// The Sessions page's search, folded into an icon until it's wanted: the
// icon (or "/" anywhere outside a field) opens the field with the cursor in
// it. It stays open while it holds text; empty, it folds back when focus
// leaves it. Escape clears it and folds it back.
const props = defineProps({
  modelValue: { type: String, default: "" },
  placeholder: { type: String, default: "Search by title, event or teammate" },
});
const emit = defineEmits(["update:modelValue"]);

const expanded = ref(Boolean(props.modelValue));
const root = ref(null);
const input = ref(null);
const toggle = ref(null);
const inputId = `st-${useId()}`;

async function expand() {
  expanded.value = true;
  await nextTick();
  input.value?.focus();
}

async function collapse({ refocus = false } = {}) {
  expanded.value = false;
  if (refocus) {
    await nextTick();
    toggle.value?.focus();
  }
}

function onFocusOut(event) {
  if (props.modelValue.trim()) return;
  if (root.value?.contains(event.relatedTarget)) return;
  collapse();
}

function onKeydown(event) {
  if (event.key !== "Escape") return;
  event.preventDefault();
  emit("update:modelValue", "");
  collapse({ refocus: true });
}

// Cleared from outside ("Clear all") while not in use: fold back.
watch(
  () => props.modelValue,
  (value) => {
    if (value.trim()) expanded.value = true;
    else if (!root.value?.contains(document.activeElement)) expanded.value = false;
  },
);

function typingIn(target) {
  if (!target || target === document.body) return false;
  const tag = target.tagName;
  return target.isContentEditable || tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

function onShortcut(event) {
  if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey || event.defaultPrevented) return;
  if (typingIn(event.target) || document.querySelector('[role="dialog"]')) return;
  event.preventDefault();
  expand();
}

onMounted(() => window.addEventListener("keydown", onShortcut));
onBeforeUnmount(() => window.removeEventListener("keydown", onShortcut));
</script>

<template>
  <div ref="root" class="st" :class="{ 'st-open': expanded }" @focusout="onFocusOut">
    <button
      v-if="!expanded"
      ref="toggle"
      type="button"
      class="st-toggle siq-fx"
      aria-label="Search sessions"
      :aria-expanded="'false'"
      aria-keyshortcuts="/"
      title="Search (/)"
      @click="expand"
    >
      <Icon name="search" :size="18" />
    </button>
    <template v-else>
      <Icon name="search" :size="16" class="st-icon" />
      <label :for="inputId" class="siq-sr-only">Search sessions</label>
      <input
        :id="inputId"
        ref="input"
        class="st-input"
        type="search"
        autocomplete="off"
        :placeholder="placeholder"
        :value="modelValue"
        @input="emit('update:modelValue', $event.target.value)"
        @keydown="onKeydown"
      />
    </template>
  </div>
</template>

<style scoped>
.st {
  position: relative;
  display: inline-flex;
  align-items: center;
}

.st-open {
  flex: 0 1 20rem;
  min-width: 12rem;
}

.st-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--siq-target);
  height: var(--siq-target);
  margin: 0;
  padding: 0;
  border: 1px solid var(--siq-border);
  border-radius: var(--siq-radius-control);
  background: var(--siq-input);
  color: var(--siq-ink-2);
  cursor: pointer;
  transition-property: border-color, color;
  transition-duration: var(--siq-duration);
  transition-timing-function: var(--siq-ease);
}

.st-toggle:hover {
  border-color: var(--siq-border-hover);
  color: var(--siq-ink);
}

.st-icon {
  position: absolute;
  left: var(--siq-space-3);
  color: var(--siq-muted);
  pointer-events: none;
}

.st-input {
  width: 100%;
  height: var(--siq-target);
  margin: 0;
  padding: 0 var(--siq-space-3) 0 36px;
  border: 1px solid var(--siq-border);
  border-radius: var(--siq-radius-control);
  background: var(--siq-input);
  color: var(--siq-ink);
  font: inherit;
  font-size: var(--siq-text-ui);
  box-sizing: border-box;
  transition-property: border-color;
  transition-duration: var(--siq-duration);
  transition-timing-function: var(--siq-ease);
}

.st-input:hover {
  border-color: var(--siq-border-hover);
}

.st-input:focus-visible {
  outline: 2px solid var(--siq-focus);
  outline-offset: 1px;
}

.st-input::placeholder {
  color: var(--siq-muted);
}

@media (prefers-reduced-motion: reduce) {
  .st-toggle,
  .st-input {
    transition: none;
  }
}
</style>
