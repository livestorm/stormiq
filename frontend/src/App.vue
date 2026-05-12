<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { RouterLink, RouterView, useRoute, useRouter } from "vue-router";
import { api } from "./api";
import SessionHero from "./components/SessionHero.vue";
import { useWorkspace } from "./store/workspace";

// Phase 4 layout.
//
// Top-level sidebar has three sections: Search / Single Analysis /
// Cross Analysis. When the active route is /single-analysis/:sessionId/*,
// a sub-nav appears under Single Analysis with the six per-session tabs
// (Overview, Transcript, Chat & Questions, Analysis, Smart Recap,
// Content Repurposing).
//
// The route's :sessionId is the source of truth for which session is
// loaded — App.vue watches it and asks the store to materialise the
// workspace when it changes. Old URLs (/transcript, /analysis, etc.)
// are handled by redirects defined in router.js.

const {
  state,
  applyBootstrap,
  hasTranscriptData,
  isTranscriptLoading,
  isTranscriptUnavailable,
  loadSessionById,
  resetWorkspace,
} = useWorkspace();
const route = useRoute();
const router = useRouter();
const logoUrl = "/brand-assets/icons/Icon-Livestorm-Tertiary-Light.png";
const sidebarCollapsed = ref(false);
const isCompactViewport = ref(false);
const COMPACT_BREAKPOINT = 1100;

// Top-level destinations. Order matches the product taxonomy.
// No icons — the user found the emoji decorations noisy.
const topNavItems = [
  { to: "/search", label: "Search" },
  { to: "/single-analysis", label: "Single Analysis" },
  { to: "/cross-analysis", label: "Cross Analysis" },
];

// Treated as "logged in" for nav-enable purposes. Local API-key mode
// counts (development convenience); blank state shows the Connect
// flow as the only interactive control.
const isAuthed = computed(
  () => Boolean(state.auth?.connectedUser || state.auth?.allowLocalApiKeyFallback),
);

// Per-session sub-nav. Rendered only on /single-analysis/:sessionId/*.
const sessionTabs = [
  { suffix: "session-overview", label: "Overview", key: "overview" },
  { suffix: "transcript", label: "Transcript", key: "transcript" },
  { suffix: "chat-questions", label: "Chat & Questions", key: "chat" },
  { suffix: "analysis", label: "Analysis", key: "analysis" },
  { suffix: "smart-recap", label: "Smart Recap", key: "recap" },
  { suffix: "content-repurposing", label: "Repurposing", key: "repurposing" },
];

const routeSessionId = computed(() => {
  const id = route.params?.sessionId;
  return id ? String(id).trim() : "";
});

const isOnSessionDetail = computed(() => Boolean(routeSessionId.value));
const isOnSingleAnalysis = computed(
  () => route.path === "/single-analysis" || route.path.startsWith("/single-analysis/"),
);

const tabsState = computed(() => {
  const transcriptReady = hasTranscriptData.value;
  const transcriptLoading = isTranscriptLoading.value;
  const transcriptUnavailable = isTranscriptUnavailable.value;
  const hasWorkspace = Boolean(state.workspace);
  const isFreshFetch = state.loading.sessionFetch && !hasWorkspace;
  return {
    overview: { disabled: !hasWorkspace && !isFreshFetch, loading: isFreshFetch, ready: hasWorkspace },
    transcript: { disabled: !hasWorkspace || transcriptUnavailable, loading: transcriptLoading, ready: transcriptReady, unavailable: transcriptUnavailable },
    chat: { disabled: !hasWorkspace, loading: false, ready: hasWorkspace },
    analysis: { disabled: !transcriptReady || transcriptUnavailable, loading: transcriptLoading, ready: transcriptReady, unavailable: transcriptUnavailable },
    recap: { disabled: !transcriptReady || transcriptUnavailable, loading: transcriptLoading, ready: transcriptReady, unavailable: transcriptUnavailable },
    repurposing: { disabled: !transcriptReady || transcriptUnavailable, loading: transcriptLoading, ready: transcriptReady, unavailable: transcriptUnavailable },
  };
});

function tabMeta(tab) {
  return tabsState.value[tab.key] || { disabled: false, loading: false, ready: false };
}

function tabHref(tab) {
  const id = routeSessionId.value;
  return id ? `/single-analysis/${id}/${tab.suffix}` : "/single-analysis";
}

function tabIsActive(tab) {
  return route.path.endsWith(`/${tab.suffix}`);
}

const isLocalApiKeyMode = computed(() => Boolean(state.auth?.allowLocalApiKeyFallback && state.apiKey));
const isOAuthMode = computed(() => !isLocalApiKeyMode.value && Boolean(state.auth?.oauthEnabled || state.auth?.connectedUser));
const isConnected = computed(() => Boolean(state.auth?.connectedUser));
const connectedBadgeLabel = computed(
  () =>
    state.auth?.connectedUser?.organizationName ||
    state.auth?.connectedUser?.fullName ||
    state.auth?.connectedUser?.email ||
    "Connected",
);

function syncViewportMode() {
  if (typeof window === "undefined") return;
  const nextCompact = window.innerWidth <= COMPACT_BREAKPOINT;
  isCompactViewport.value = nextCompact;
  sidebarCollapsed.value = nextCompact;
}

onMounted(async () => {
  syncViewportMode();
  window.addEventListener("resize", syncViewportMode);
  try {
    const bootstrap = await api.bootstrap();
    applyBootstrap(bootstrap);
    if (route.path === "/auth/callback") {
      await router.replace("/single-analysis");
    }
  } catch (_error) {
    // Ignore bootstrap failures so manual entry still works.
  }
  // If the user deep-links into a session detail page (shared link or
  // page reload), make sure the store has the matching workspace.
  if (routeSessionId.value) {
    loadSessionById(routeSessionId.value).catch(() => {});
  }
});

onBeforeUnmount(() => {
  if (typeof window !== "undefined") {
    window.removeEventListener("resize", syncViewportMode);
  }
});

// Watch the URL :sessionId — switching sessions is a route navigation,
// not a button click, so the store needs to react to it.
watch(routeSessionId, async (next, previous) => {
  if (!next || next === previous) return;
  if (state.workspace?.sessionId === next) return;
  try {
    await loadSessionById(next);
  } catch (_error) {
    // Errors surface in state.error / state.transcriptUnavailableReason.
  }
});

function handleConnectClick() {
  const returnTo = route.path || "/single-analysis";
  window.location.href = `/api/auth/livestorm/start?returnTo=${encodeURIComponent(returnTo)}`;
}

async function handleLogoutClick() {
  try {
    await api.logout();
  } catch (_error) {
    // Ignore logout failures and clear local state anyway.
  }
  state.auth.connectedUser = null;
  state.apiKey = "";
  resetWorkspace();
  router.push("/single-analysis");
}

function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value;
}
</script>

<template>
  <div
    class="layout"
    data-colors-semantic="dark"
    :class="{
      'layout-sidebar-collapsed': sidebarCollapsed,
      'layout-compact': isCompactViewport,
      'layout-compact-sidebar-open': isCompactViewport && !sidebarCollapsed,
    }"
  >
    <div
      v-if="isCompactViewport && !sidebarCollapsed"
      class="sidebar-backdrop"
      aria-hidden="true"
      @click="sidebarCollapsed = true"
    ></div>

    <aside class="sidebar" :class="{ collapsed: sidebarCollapsed, compact: isCompactViewport }">
      <div class="sidebar-brand" :class="{ collapsed: sidebarCollapsed }">
        <img :src="logoUrl" alt="StormIQ" class="brand-logo" />
        <div v-if="!sidebarCollapsed" class="brand-copy">
          <h1>StormIQ</h1>
        </div>
        <button
          v-if="isCompactViewport"
          type="button"
          class="sidebar-toggle sidebar-toggle-inside"
          :aria-expanded="String(!sidebarCollapsed)"
          :aria-label="sidebarCollapsed ? 'Open navigation' : 'Close navigation'"
          @click="toggleSidebar"
        >
          <span aria-hidden="true">{{ sidebarCollapsed ? "☰" : "✕" }}</span>
        </button>
      </div>

      <template v-if="!sidebarCollapsed">
        <nav class="sidebar-nav" aria-label="Primary">
          <RouterLink
            v-for="item in topNavItems"
            :key="item.to"
            :to="item.to"
            class="sidebar-nav-item"
            :class="{
              'sidebar-nav-item-active':
                route.path === item.to ||
                (item.to === '/single-analysis' && isOnSingleAnalysis),
              'sidebar-nav-item-disabled': !isAuthed,
            }"
            :aria-disabled="!isAuthed"
            :tabindex="isAuthed ? 0 : -1"
            @click.capture="!isAuthed && $event.preventDefault()"
          >
            <span class="sidebar-nav-label">{{ item.label }}</span>
          </RouterLink>
        </nav>

        <!-- Per-session sub-nav. Visible only when on a session detail
             route. Tabs point at /single-analysis/:sessionId/<suffix>. -->
        <nav v-if="isOnSessionDetail" class="sidebar-subnav" aria-label="Session tabs">
          <p class="sidebar-subnav-title">Session</p>
          <RouterLink
            v-for="tab in sessionTabs"
            :key="tab.key"
            :to="tabHref(tab)"
            class="sidebar-subnav-item"
            :class="{
              'sidebar-subnav-item-active': tabIsActive(tab),
              disabled: tabMeta(tab).disabled,
              loading: tabMeta(tab).loading,
            }"
          >
            <span class="sidebar-subnav-label">{{ tab.label }}</span>
            <span v-if="tabMeta(tab).loading" class="top-nav-status top-nav-status-loading" aria-hidden="true"></span>
            <span v-else-if="tabMeta(tab).unavailable" class="top-nav-status top-nav-status-unavailable" aria-hidden="true"></span>
            <span v-else-if="tabMeta(tab).ready" class="top-nav-status top-nav-status-ready" aria-hidden="true"></span>
          </RouterLink>
        </nav>

        <!-- Bottom block: auth + beta notice live together in a footer
             so they don't drift apart visually. Without the wrapper,
             `margin-top: auto` only pinned one of them and the other
             floated in the middle of the sidebar. -->
        <div class="sidebar-footer">
          <div class="sidebar-account">
            <div v-if="isLocalApiKeyMode" class="sidebar-account-mode">
              <p class="sidebar-account-title">Local mode</p>
              <p class="sidebar-account-copy">Using <code>LS_API_KEY</code> fallback.</p>
            </div>
            <template v-else-if="isOAuthMode">
              <button
                v-if="!isConnected"
                class="primary fetch-button"
                type="button"
                @click="handleConnectClick"
              >
                Connect with Livestorm
              </button>
              <div v-else class="oauth-connected-card">
                <div class="oauth-connected-info">
                  <div class="oauth-user-badge">
                    <span>{{ connectedBadgeLabel }}</span>
                  </div>
                  <div class="oauth-connected-title">Connected with Livestorm</div>
                </div>
                <button
                  type="button"
                  class="oauth-disconnect-button"
                  aria-label="Disconnect from Livestorm"
                  @click="handleLogoutClick"
                >
                  Disconnect
                </button>
              </div>
            </template>
            <div v-else class="field-group">
              <input v-model="state.apiKey" type="password" placeholder="Livestorm API Key" />
            </div>

            <p v-if="state.error" class="error-text sidebar-error">{{ state.error }}</p>
          </div>

          <div class="sidebar-beta-notice">
            <p class="sidebar-beta-title">Beta notice</p>
            <p class="sidebar-beta-copy">
              Early-access helper, not an official Livestorm product. Review outputs before relying on them.
            </p>
            <RouterLink to="/beta-info" class="sidebar-beta-link">Read more</RouterLink>
          </div>
        </div>
      </template>
    </aside>

    <main class="main-content">
      <div v-if="isCompactViewport" class="compact-topbar">
        <button
          type="button"
          class="compact-topbar-toggle"
          :aria-expanded="String(!sidebarCollapsed)"
          :aria-label="sidebarCollapsed ? 'Open navigation' : 'Close navigation'"
          @click="toggleSidebar"
        >
          <span aria-hidden="true">{{ sidebarCollapsed ? "☰" : "✕" }}</span>
        </button>
        <button
          type="button"
          class="compact-topbar-logo-button"
          @click="toggleSidebar"
        >
          <img :src="logoUrl" alt="StormIQ" class="compact-topbar-logo" />
        </button>
      </div>

      <!-- Hero header for every per-session route. Self-hides when the
           workspace hasn't loaded yet, so the existing per-tab loaders
           still take the screen on the first paint. -->
      <SessionHero v-if="isOnSessionDetail" />
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.sidebar-nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 0;
  margin-top: 8px;
}

.sidebar-nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  border-radius: 8px;
  text-decoration: none;
  color: rgba(255, 255, 255, 0.78);
  font-weight: 500;
  transition: background 120ms ease-out, color 120ms ease-out;
}

.sidebar-nav-item:hover {
  background: rgba(255, 255, 255, 0.06);
  color: #ffffff;
}

.sidebar-nav-item-active {
  background: rgba(11, 66, 195, 0.18);
  color: #ffffff;
}

/* Disabled state: rendered when the user hasn't connected yet. Visually
   muted; the @click.capture handler in the template stops navigation. */
.sidebar-nav-item-disabled {
  opacity: 0.35;
  cursor: not-allowed;
  pointer-events: none;
}

.sidebar-subnav {
  margin-top: 8px;
  padding: 8px 0 12px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.sidebar-subnav-title {
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-size: 11px;
  color: rgba(255, 255, 255, 0.5);
  padding: 8px 14px 4px;
  margin: 0;
}

.sidebar-subnav-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 14px;
  border-radius: 6px;
  text-decoration: none;
  color: rgba(255, 255, 255, 0.7);
  font-size: 14px;
  transition: background 120ms ease-out, color 120ms ease-out;
}

.sidebar-subnav-item:hover {
  background: rgba(255, 255, 255, 0.04);
  color: #ffffff;
}

.sidebar-subnav-item-active {
  background: rgba(255, 255, 255, 0.08);
  color: #ffffff;
}

.sidebar-subnav-item.disabled {
  opacity: 0.45;
  pointer-events: none;
}

.sidebar-footer {
  margin-top: auto;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.sidebar-account {
  padding-top: 12px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}

.sidebar-account-mode {
  padding: 8px 14px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.04);
}

.sidebar-account-title {
  margin: 0;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: rgba(255, 255, 255, 0.55);
}

.sidebar-account-copy {
  margin: 4px 0 0;
  font-size: 13px;
  color: rgba(255, 255, 255, 0.75);
}

.sidebar-account-copy code {
  background: rgba(255, 255, 255, 0.08);
  padding: 0 4px;
  border-radius: 3px;
}

.sidebar-error {
  margin-top: 12px;
  padding: 0 14px;
}
</style>
