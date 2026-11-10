<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { RouterLink } from "vue-router";
import Icon from "./Icon.vue";
import StormIqMark from "./StormIqMark.vue";
import ForLivestorm from "./ForLivestorm.vue";
import ThemeToggle from "./ThemeToggle.vue";
import UiButton from "./UiButton.vue";

// The v1 shell's top bar: the StormIQ wordmark, the Beta tag and the "for
// Livestorm" lockup, the main navigation, the theme toggle and the account
// button. It wraps onto two lines on small
// screens. App.vue owns the signed-in state and the sign-in/out flows; this
// component only shows them and emits "connect" / "logout".
const props = defineProps({
  // [{ to, label, icon?, active, current?, disabled? }]. `current` is the
  // aria-current value ("page" or "true" for a section); it defaults to
  // "page" when the item is active.
  items: { type: Array, default: () => [] },
  // { name, detail } when signed in, otherwise null.
  account: { type: Object, default: null },
  // Show "Connect with Livestorm" when nobody is signed in.
  canConnect: { type: Boolean, default: false },
  // Local development with an API key: no account menu.
  localMode: { type: Boolean, default: false },
  // Where "Use the classic design" points (the current path with ?ui=classic).
  classicHref: { type: String, default: "/?ui=classic" },
});

const emit = defineEmits(["connect", "logout"]);

const menuOpen = ref(false);
const accountRoot = ref(null);

const initials = computed(() => {
  const name = String(props.account?.name || "").trim();
  if (!name) return "?";
  const words = name.includes("@") ? [name] : name.split(/\s+/);
  return words
    .slice(0, 2)
    .map((word) => word[0])
    .join("")
    .toUpperCase();
});

const accountLabel = computed(() => {
  const parts = [props.account?.name, props.account?.detail].filter(Boolean);
  return `Account: ${parts.join(", ")}`;
});

function closeMenu({ focusButton = false } = {}) {
  menuOpen.value = false;
  if (focusButton) accountRoot.value?.querySelector("button")?.focus();
}

function onDocumentPointer(event) {
  if (accountRoot.value && !accountRoot.value.contains(event.target)) closeMenu();
}

function onFocusOut(event) {
  if (accountRoot.value && event.relatedTarget && !accountRoot.value.contains(event.relatedTarget)) closeMenu();
}

watch(menuOpen, (open) => {
  if (open) document.addEventListener("pointerdown", onDocumentPointer);
  else document.removeEventListener("pointerdown", onDocumentPointer);
});

// Signed out while the menu was open (a 401, for example): the menu goes away
// with the account, so close it and drop the outside-click listener too.
watch(
  () => props.account,
  (account) => {
    if (!account) menuOpen.value = false;
  },
);

onBeforeUnmount(() => document.removeEventListener("pointerdown", onDocumentPointer));

function onLogout() {
  closeMenu();
  emit("logout");
}
</script>

<template>
  <header class="siq-topbar">
    <div class="siq-topbar-inner">
      <div class="siq-topbar-brand">
        <RouterLink to="/single-analysis" class="siq-topbar-home siq-fx" aria-label="StormIQ, go to Sessions">
          <StormIqMark :mark="false" />
        </RouterLink>
        <span class="siq-topbar-beta">Beta</span>
        <ForLivestorm />
      </div>

      <nav class="siq-topbar-nav" aria-label="Main">
        <RouterLink v-for="item in items" :key="item.label" v-slot="{ href, navigate }" :to="item.to" custom>
          <a
            :href="href"
            class="siq-topbar-link siq-fx"
            :class="{ 'siq-topbar-link-active': item.active, 'siq-topbar-link-disabled': item.disabled }"
            :aria-current="item.current || (item.active ? 'page' : undefined)"
            :aria-disabled="item.disabled ? 'true' : undefined"
            @click="item.disabled ? $event.preventDefault() : navigate($event)"
          >
            <Icon v-if="item.icon" :name="item.icon" :size="14" />
            {{ item.label }}
          </a>
        </RouterLink>
      </nav>

      <div class="siq-topbar-end">
        <ThemeToggle />

        <span v-if="localMode" class="siq-topbar-local">Local mode</span>

        <UiButton v-else-if="!account && canConnect" variant="primary" @click="emit('connect')">
          Connect with Livestorm
        </UiButton>

        <div
          v-else-if="account"
          ref="accountRoot"
          class="siq-account"
          @keydown.esc="closeMenu({ focusButton: true })"
          @focusout="onFocusOut"
        >
          <button
            type="button"
            class="siq-account-button siq-fx"
            :aria-label="accountLabel"
            :aria-expanded="String(menuOpen)"
            aria-controls="siq-account-menu"
            @click="menuOpen = !menuOpen"
          >
            <span class="siq-account-avatar" aria-hidden="true">{{ initials }}</span>
            <span class="siq-account-text" aria-hidden="true">
              <span class="siq-account-name">{{ account.name }}</span>
              <span v-if="account.detail" class="siq-account-detail">{{ account.detail }}</span>
            </span>
            <Icon :name="menuOpen ? 'chevron-up' : 'chevron-down'" :size="14" class="siq-account-chevron" />
          </button>
          <div v-show="menuOpen" id="siq-account-menu" class="siq-account-menu">
            <p class="siq-account-menu-note">Connected with Livestorm</p>
            <a :href="classicHref" class="siq-account-item siq-fx">
              <Icon name="refresh-cw" :size="16" />
              Use the classic design
            </a>
            <button type="button" class="siq-account-item siq-fx" @click="onLogout">
              <Icon name="log-out" :size="16" />
              Disconnect
            </button>
          </div>
        </div>
      </div>
    </div>
  </header>
</template>

<style scoped>
.siq-topbar {
  background: var(--siq-topbar);
  box-shadow: inset 0 -1px 0 var(--siq-hairline);
  position: relative;
  z-index: 20;
}

.siq-topbar-inner {
  max-width: var(--siq-container);
  margin: 0 auto;
  padding: 0 var(--siq-gutter);
  min-height: var(--siq-topbar-height);
  box-sizing: border-box;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--siq-space-2) var(--siq-space-6);
}

.siq-topbar-brand {
  display: flex;
  align-items: center;
  gap: 10px;
}

.siq-topbar-home {
  display: flex;
  align-items: center;
  min-height: var(--siq-target);
  border-radius: var(--siq-radius-control);
  color: var(--siq-ink);
  text-decoration: none;
}

.siq-topbar-beta {
  font-size: var(--siq-text-tag);
  line-height: var(--siq-text-tag-lh);
  font-weight: var(--siq-weight-medium);
  letter-spacing: var(--siq-label-tracking);
  text-transform: uppercase;
  color: var(--siq-accent);
  box-shadow: inset 0 0 0 1px var(--siq-hairline-head);
  border-radius: var(--siq-radius-pill);
  padding: 2px 8px;
}

.siq-topbar-nav {
  display: flex;
  flex-wrap: wrap;
  align-items: stretch;
  gap: var(--siq-space-1);
  align-self: stretch;
}

.siq-topbar-link {
  display: inline-flex;
  align-items: center;
  gap: var(--siq-space-2);
  padding: 0 var(--siq-space-3);
  min-height: var(--siq-topbar-height);
  box-sizing: border-box;
  color: var(--siq-ink-2);
  font-size: var(--siq-text-nav);
  line-height: var(--siq-text-nav-lh);
  font-weight: var(--siq-weight-medium);
  text-decoration: none;
}

.siq-topbar-link:hover {
  color: var(--siq-ink);
}

.siq-topbar-link-active {
  color: var(--siq-ink);
  box-shadow: inset 0 -2px 0 var(--siq-accent);
}

.siq-topbar-link-disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.siq-topbar-end {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: var(--siq-space-2);
}

.siq-topbar-local {
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
}

.siq-account {
  position: relative;
}

.siq-account-button {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: var(--siq-target);
  max-width: 280px;
  padding: 4px 8px;
  border: 0;
  border-radius: var(--siq-radius-control);
  background: transparent;
  color: var(--siq-ink);
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.siq-account-button:hover {
  background: var(--siq-surface-2);
}

.siq-account-avatar {
  flex: none;
  width: 32px;
  height: 32px;
  border-radius: var(--siq-radius-pill);
  background: var(--siq-selected-bg);
  color: var(--siq-selected-ink);
  font-size: var(--siq-text-caption);
  font-weight: var(--siq-weight-semibold);
  display: flex;
  align-items: center;
  justify-content: center;
}

.siq-account-text {
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.siq-account-name,
.siq-account-detail {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.siq-account-name {
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-small-lh);
  font-weight: var(--siq-weight-medium);
}

.siq-account-detail {
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  color: var(--siq-muted);
}

.siq-account-chevron {
  color: var(--siq-muted);
}

.siq-account-menu {
  position: absolute;
  right: 0;
  top: calc(100% + var(--siq-space-2));
  min-width: 240px;
  padding: var(--siq-space-2);
  border-radius: var(--siq-radius-control);
  background: var(--siq-card);
  box-shadow: var(--siq-card-shadow), inset 0 0 0 1px var(--siq-hairline-head);
  display: flex;
  flex-direction: column;
}

.siq-account-menu-note {
  margin: 0;
  padding: var(--siq-space-2) var(--siq-space-3);
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  color: var(--siq-muted);
}

.siq-account-item {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: var(--siq-target);
  padding: 0 var(--siq-space-3);
  border: 0;
  border-radius: var(--siq-radius-control);
  background: transparent;
  color: var(--siq-ink);
  font: inherit;
  font-size: var(--siq-text-ui);
  text-align: left;
  text-decoration: none;
  cursor: pointer;
}

.siq-account-item:hover {
  background: var(--siq-card-hover);
}

/* Windows High Contrast drops box-shadows: draw the edges with borders. */
@media (forced-colors: active) {
  .siq-topbar {
    border-bottom: 1px solid CanvasText;
  }

  .siq-topbar-link-active {
    border-bottom: 2px solid Highlight;
  }

  .siq-topbar-beta,
  .siq-account-menu {
    border: 1px solid CanvasText;
  }
}

@media (max-width: 720px) {
  .siq-topbar-inner {
    padding: var(--siq-space-2) var(--siq-space-4) 0;
    gap: var(--siq-space-1) var(--siq-space-3);
  }

  /* The brand takes the first row; the nav and the controls share the second,
     in DOM order, so Tab moves through them in the order they appear. */
  .siq-topbar-brand {
    flex-basis: 100%;
  }

  .siq-topbar-link {
    min-height: 48px;
    padding: 0 var(--siq-space-2);
  }

  .siq-account-text {
    display: none;
  }
}

/* On paper only the "StormIQ for Livestorm" lockup stays: the navigation
   and the account menu do nothing there. */
@media print {
  .siq-topbar-nav,
  .siq-topbar-end {
    display: none;
  }
}
</style>
