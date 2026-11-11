<script setup>
import { nextTick, onBeforeUnmount, ref, watch } from "vue";

// A small dialog that floats under the control that opened it (`anchor`):
// the Sessions page's column filters, event picker and "Sort & filter"
// sheet. It is teleported to <body>, so the flush card's clipping never
// cuts it, and placed with `position: fixed` from the anchor's box, kept on
// screen and flipped above when there's no room below.
//
// Focus moves into it when it opens (to `[data-autofocus]`, else the first
// control) and Tab stays inside. Escape, a click outside or the parent
// setting `open` false closes it; focus then goes back to the anchor,
// unless a click outside took it elsewhere. With `sheet`, it becomes a
// bottom sheet on a phone (≤720px).
const props = defineProps({
  open: { type: Boolean, default: false },
  anchor: { type: Object, default: null },
  label: { type: String, required: true },
  // Which edge of the anchor the panel lines up with.
  align: { type: String, default: "start", validator: (value) => ["start", "end"].includes(value) },
  sheet: { type: Boolean, default: false },
});
const emit = defineEmits(["close"]);

const panel = ref(null);
const position = ref({});
const GAP = 4;
const EDGE = 8;
const FOCUSABLE = 'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea, a[href], [tabindex]:not([tabindex="-1"])';

let closedByOutsideClick = false;
// The anchor it opened from: the parent may drop `anchor` as it closes.
let opener = null;

function place() {
  const element = panel.value;
  const anchor = props.anchor;
  if (!props.open || !element || !anchor?.getBoundingClientRect) return;
  const box = anchor.getBoundingClientRect();
  const width = element.offsetWidth;
  const height = element.offsetHeight;
  const viewportW = document.documentElement.clientWidth || window.innerWidth;
  const viewportH = window.innerHeight;
  let left = props.align === "end" ? box.right - width : box.left;
  left = Math.max(EDGE, Math.min(left, viewportW - width - EDGE));
  let top = box.bottom + GAP;
  if (top + height > viewportH - EDGE && box.top - height - GAP >= EDGE) top = box.top - height - GAP;
  position.value = {
    top: `${Math.round(top)}px`,
    left: `${Math.round(left)}px`,
    maxHeight: `${Math.max(160, Math.round(viewportH - top - EDGE))}px`,
  };
}

function focusables() {
  return panel.value ? [...panel.value.querySelectorAll(FOCUSABLE)].filter((node) => node.offsetParent !== null || node === document.activeElement) : [];
}

function onKeydown(event) {
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    emit("close");
    return;
  }
  if (event.key !== "Tab") return;
  const nodes = focusables();
  if (!nodes.length) {
    event.preventDefault();
    return;
  }
  const first = nodes[0];
  const last = nodes[nodes.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function onPointerDown(event) {
  const target = event.target;
  if (panel.value?.contains(target) || props.anchor?.contains?.(target)) return;
  closedByOutsideClick = true;
  emit("close");
}

function listen(on) {
  const method = on ? "addEventListener" : "removeEventListener";
  document[method]("pointerdown", onPointerDown, true);
  window[method]("resize", place);
  window[method]("scroll", place, true);
}

watch(
  () => props.open,
  async (open, wasOpen) => {
    if (open) {
      closedByOutsideClick = false;
      opener = props.anchor;
      listen(true);
      await nextTick();
      place();
      const target = panel.value?.querySelector("[data-autofocus]") || focusables()[0] || panel.value;
      target?.focus({ preventScroll: true });
    } else if (wasOpen) {
      listen(false);
      if (!closedByOutsideClick) opener?.focus?.({ preventScroll: true });
      opener = null;
    }
  },
  { immediate: true },
);

onBeforeUnmount(() => listen(false));

defineExpose({ place });
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="fp-scrim" :class="{ 'fp-has-sheet': sheet }" aria-hidden="true" />
    <div
      v-if="open"
      ref="panel"
      class="fp"
      :class="{ 'fp-sheet': sheet }"
      role="dialog"
      :aria-label="label"
      tabindex="-1"
      :style="position"
      @keydown="onKeydown"
    >
      <slot />
    </div>
  </Teleport>
</template>

<style scoped>
.fp {
  position: fixed;
  z-index: 30;
  box-sizing: border-box;
  min-width: 15rem;
  max-width: min(22rem, calc(100vw - 16px));
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: var(--siq-space-2);
  border-radius: var(--siq-radius-control);
  background: var(--siq-card);
  color: var(--siq-ink);
  font-family: var(--siq-font);
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  box-shadow: var(--siq-card-shadow), inset 0 0 0 1px var(--siq-hairline-head);
}

.fp:focus {
  outline: none;
}

.fp-scrim {
  display: none;
}

/* On a phone, the sheet rises from the bottom over a dimmed page. */
@media (max-width: 720px) {
  .fp-sheet {
    top: auto !important;
    left: 0 !important;
    right: 0;
    bottom: 0;
    max-width: none;
    max-height: 85vh !important;
    padding: var(--siq-space-4);
    border-radius: var(--siq-radius-card) var(--siq-radius-card) 0 0;
  }
  .fp-scrim.fp-has-sheet {
    display: block;
    position: fixed;
    inset: 0;
    z-index: 29;
    background: var(--siq-ink);
    opacity: 0.32;
  }
}

@media (forced-colors: active) {
  .fp {
    border: 1px solid CanvasText;
  }
}
</style>
