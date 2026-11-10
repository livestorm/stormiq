<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { RouterLink, RouterView, useRoute, useRouter } from "vue-router";
import { api, onUnauthorized } from "./api";
import SessionHero from "./components/SessionHero.vue";
import StormIqMark from "./components/ui/StormIqMark.vue";
import ForLivestorm from "./components/ui/ForLivestorm.vue";
import TopBar from "./components/ui/TopBar.vue";
import { isV1 } from "./design/uiVersion";
import { useWorkspace } from "./store/workspace";

// Layout.
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
// The v1 design (opt-in with ?ui=v1, src/design/uiVersion.js) renders the
// top-bar shell instead of the classic sidebar. Fixed for the page load.
const v1 = isV1();
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

// Per-session sub-nav. Rendered only on /single-analysis/:sessionId/*. The
// v1 shell's copy starts with the v1 Report, so its classic tabs lead back.
const v1SessionTabs = computed(() => [{ suffix: "report", label: "Report", key: "report" }, ...sessionTabs]);
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
// Hero only renders on the overview tab — keeps the Transcript /
// Analysis / Recap tabs focused on their own content without a tall
// banner pushing it below the fold.
const isOnSessionOverview = computed(
  () => Boolean(routeSessionId.value) && route.path.endsWith("/session-overview"),
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

// ── v1 shell ──────────────────────────────────────────────────────────────
// Top-bar navigation, mapped onto the existing routes until the rebuilt
// screens land: "Sessions" is the session list (and every session page),
// "Add session" is Search.
const v1NavItems = computed(() => {
  const items = [
    {
      to: "/single-analysis",
      label: "Sessions",
      active: isOnSingleAnalysis.value,
      // "page" on the list itself; "true" (the current section) on a session's pages.
      current: route.path === "/single-analysis" ? "page" : isOnSingleAnalysis.value ? "true" : undefined,
      disabled: !isAuthed.value,
    },
    { to: "/search", label: "Add session", icon: "plus", active: route.path === "/search", disabled: !isAuthed.value },
  ];
  if (state.auth?.isAdmin) items.push({ to: "/admin", label: "Admin", active: route.path === "/admin" });
  return items;
});

const v1Account = computed(() => {
  const user = state.auth?.connectedUser;
  if (!user) return null;
  const name = user.fullName || user.email || "Connected";
  const detail = user.organizationName && user.organizationName !== name ? user.organizationName : "";
  return { name, detail };
});

// Pages built for the v1 design (route meta `v1Native`) fill the page; every
// other view keeps its classic dark styling inside a framed area until it is
// rebuilt.
const isV1NativeRoute = computed(() => Boolean(route.meta?.v1Native));
const classicDesignHref = computed(() => `${route.path || "/"}?ui=classic`);

function syncViewportMode() {
  if (typeof window === "undefined" || v1) return;
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
      // Full page navigation (not router.replace) so the browser fetches
      // fresh index.html. Otherwise a session that started on an old
      // cached bundle keeps running it after OAuth, leaving the user on
      // a pre-restructure UI with broken hashed-asset references.
      window.location.replace("/single-analysis");
      return;
    }
    // Local dev: LS_API_KEY is set but no OAuth session exists yet.
    // Auto-seed the identity from /me so the user lands logged in
    // without having to manually visit /api/dev/auto-login or go
    // through an OAuth flow that redirects to production.
    if (bootstrap?.auth?.allowLocalApiKeyFallback && !bootstrap?.auth?.connectedUser) {
      window.location.replace("/api/dev/auto-login");
      return;
    }
  } catch (error) {
    console.error("Bootstrap failed", error);
    state.error = "StormIQ could not load your account. Check your connection and reload the page.";
  }
  // If the user deep-links into a session detail page (shared link or
  // page reload), make sure the store has the matching workspace. A v1
  // page (the Report) loads its own data instead.
  if (routeSessionId.value && !isV1NativeRoute.value) {
    loadSessionById(routeSessionId.value).catch(logSessionLoadFailure);
  }
});

// The store already put the message in state.error / the transcript
// banner; keep the details in the console for debugging.
function logSessionLoadFailure(error) {
  console.warn("Session load failed", error);
}

// Any 401 means the Livestorm session expired or was revoked (or the
// visitor never connected): drop the signed-in state and ask them to
// connect. The page stays where it is, so the sidebar's Connect button
// brings them back to it (a shared session link keeps working).
onUnauthorized(() => {
  const wasSignedIn = Boolean(state.auth.connectedUser);
  state.auth.connectedUser = null;
  state.apiKey = "";
  resetWorkspace();
  state.error = wasSignedIn
    ? "Your Livestorm session has expired. Please reconnect."
    : "Please connect with Livestorm to open this page.";
});

onBeforeUnmount(() => {
  if (typeof window !== "undefined") {
    window.removeEventListener("resize", syncViewportMode);
  }
});

// Watch the URL :sessionId — switching sessions is a route navigation,
// not a button click, so the store needs to react to it. Pages built for
// v1 (the Report) read their own data and never load the classic workspace;
// moving from one of them to a classic tab of the same session loads it then.
watch([routeSessionId, isV1NativeRoute], async ([next, v1Native]) => {
  if (!next || v1Native) return;
  if (state.workspace?.sessionId === next) return;
  try {
    await loadSessionById(next);
  } catch (error) {
    logSessionLoadFailure(error);
  }
});

function handleConnectClick() {
  const returnTo = route.path || "/single-analysis";
  window.location.href = `/api/auth/livestorm/start?returnTo=${encodeURIComponent(returnTo)}`;
}

async function handleLogoutClick() {
  try {
    await api.logout();
  } catch (error) {
    // Clear local state anyway: the user asked to leave.
    console.warn("Logout request failed", error);
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
  <div v-if="v1" class="siq-app">
    <a class="siq-skip siq-fx" href="#siq-main">Skip to content</a>
    <TopBar
      :items="v1NavItems"
      :account="v1Account"
      :can-connect="isOAuthMode"
      :local-mode="isLocalApiKeyMode"
      :classic-href="classicDesignHref"
      @connect="handleConnectClick"
      @logout="handleLogoutClick"
    />

    <main id="siq-main" class="siq-main" tabindex="-1">
      <p v-if="state.error" class="siq-alert" role="alert">{{ state.error }}</p>

      <RouterView v-if="isV1NativeRoute" />
      <template v-else>
        <!-- Per-session tabs, as in the classic sidebar's sub-nav. -->
        <nav v-if="isOnSessionDetail" class="siq-session-tabs" aria-label="Session sections">
          <!-- `custom` so a disabled tab can refuse the click before the
               router sees it (a plain @click on RouterLink runs after its
               own navigate handler). -->
          <RouterLink
            v-for="tab in v1SessionTabs"
            :key="tab.key"
            v-slot="{ href, navigate }"
            :to="tabHref(tab)"
            custom
          >
            <a
              :href="href"
              class="siq-session-tab siq-fx"
              :class="{ 'siq-session-tab-disabled': tabMeta(tab).disabled }"
              :aria-current="tabIsActive(tab) ? 'page' : undefined"
              :aria-disabled="tabMeta(tab).disabled ? 'true' : undefined"
              @click="tabMeta(tab).disabled ? $event.preventDefault() : navigate($event)"
            >
              {{ tab.label }}
              <span v-if="tabMeta(tab).loading" class="siq-session-tab-note">Loading</span>
              <span v-else-if="tabMeta(tab).unavailable" class="siq-session-tab-note">Unavailable</span>
            </a>
          </RouterLink>
        </nav>

        <!-- Not rebuilt yet: the classic view, in its own dark frame. -->
        <div class="siq-classic-frame" data-colors-semantic="dark">
          <SessionHero v-if="isOnSessionOverview" />
          <RouterView />
        </div>
      </template>
    </main>

    <footer class="siq-footer">
      <p>
        StormIQ is an early-access helper, not an official Livestorm product. Review outputs before relying on them.
        <RouterLink to="/beta-info" class="siq-footer-link siq-fx">Read more</RouterLink>
        ·
        <a :href="classicDesignHref" class="siq-footer-link siq-fx">Use the classic design</a>
      </p>
    </footer>
  </div>

  <div
    v-else
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
        <StormIqMark v-if="sidebarCollapsed" :wordmark="false" :size="32" class="brand-logo" role="img" aria-label="StormIQ" />
        <div v-else class="brand-copy">
          <h1>StormIQ</h1>
          <ForLivestorm :rule="false" :height="16" />
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
          <RouterLink
            v-if="state.auth?.isAdmin"
            to="/admin"
            class="sidebar-nav-item sidebar-admin-item"
            :class="{ 'sidebar-nav-item-active': route.path === '/admin' }"
          >
            <span class="sidebar-nav-label">Admin</span>
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
          <StormIqMark :wordmark="false" :size="38" class="compact-topbar-logo" role="img" aria-label="StormIQ" />
        </button>
      </div>

      <!-- Hero header for the Session Overview tab only. Self-hides
           when the workspace hasn't loaded yet, so the existing per-tab
           loaders still take the screen on the first paint. -->
      <SessionHero v-if="isOnSessionOverview" />
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

.sidebar-admin-item {
  opacity: 0.75;
  font-size: 13px;
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

<style scoped>
/* ── v1 shell (only rendered when the v1 switch is on) ─────────────────── */
.siq-app {
  min-height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--siq-page);
  color: var(--siq-ink);
  font-family: var(--siq-font);
}

.siq-skip {
  position: absolute;
  left: var(--siq-space-4);
  top: -100px;
  z-index: 50;
  padding: var(--siq-space-2) var(--siq-space-4);
  border-radius: var(--siq-radius-control);
  background: var(--siq-card);
  color: var(--siq-accent);
}

.siq-skip:focus {
  top: var(--siq-space-2);
}

.siq-main {
  flex: 1 0 auto;
  width: 100%;
  max-width: var(--siq-container);
  margin: 0 auto;
  padding: var(--siq-space-8) var(--siq-gutter) var(--siq-space-12);
  box-sizing: border-box;
  min-width: 0;
}

.siq-main:focus {
  outline: none;
}

.siq-alert {
  margin: 0 0 var(--siq-space-6);
  padding: var(--siq-space-3) var(--siq-space-4);
  border-radius: var(--siq-radius-control);
  background: var(--siq-low-tint);
  color: var(--siq-low);
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
}

.siq-session-tabs {
  display: flex;
  gap: var(--siq-space-1);
  margin-bottom: var(--siq-space-6);
  overflow-x: auto;
  box-shadow: inset 0 -1px 0 var(--siq-hairline);
}

.siq-session-tab {
  display: inline-flex;
  align-items: center;
  gap: var(--siq-space-2);
  flex: none;
  min-height: 52px;
  padding: 0 var(--siq-space-3);
  color: var(--siq-ink-2);
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  font-weight: var(--siq-weight-medium);
  text-decoration: none;
}

.siq-session-tab:hover {
  color: var(--siq-ink);
}

.siq-session-tab[aria-current="page"] {
  color: var(--siq-ink);
  box-shadow: inset 0 -2px 0 var(--siq-accent);
}

/* The row scrolls sideways, which clips anything drawn outside it: draw the
   focus ring inside the tab. */
.siq-session-tab:focus-visible {
  outline-offset: -3px;
}

.siq-session-tab-disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.siq-session-tab-note {
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  font-weight: var(--siq-weight-regular);
  color: var(--siq-muted);
}

/* Classic views keep their dark styling until each is rebuilt. */
.siq-classic-frame {
  color-scheme: dark;
  padding: var(--siq-space-6);
  border-radius: var(--siq-radius-card);
  background: var(--color-surface-neutral-100);
  color: var(--color-text-neutral-base);
  font-family: "Object Sans", Inter, system-ui, sans-serif;
  min-width: 0;
}

.siq-footer {
  box-shadow: inset 0 1px 0 var(--siq-hairline);
}

.siq-footer p {
  max-width: var(--siq-container);
  margin: 0 auto;
  padding: var(--siq-space-5) var(--siq-gutter);
  box-sizing: border-box;
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  color: var(--siq-muted);
}

.siq-footer-link {
  color: var(--siq-accent);
  text-decoration: underline;
  text-underline-offset: 2px;
  border-radius: var(--siq-radius-inline);
}

.siq-footer-link:hover {
  color: var(--siq-accent-hover);
}

/* Windows High Contrast drops box-shadows: mark the current tab with a border. */
@media (forced-colors: active) {
  .siq-session-tabs {
    border-bottom: 1px solid CanvasText;
  }

  .siq-session-tab[aria-current="page"] {
    border-bottom: 2px solid Highlight;
  }
}

@media (max-width: 720px) {
  .siq-main {
    padding: var(--siq-space-6) var(--siq-space-4) var(--siq-space-8);
  }

  .siq-classic-frame {
    padding: var(--siq-space-4);
  }

  .siq-footer p {
    padding: var(--siq-space-4);
  }
}
</style>
