<script setup>
import { computed, defineAsyncComponent, onMounted, ref, watch } from "vue";
import { RouterLink, RouterView, useRoute, useRouter } from "vue-router";
import { api, onUnauthorized } from "./api";
import SessionHero from "./components/SessionHero.vue";
import TopBar from "./components/ui/TopBar.vue";
import { showsLanding } from "./design/landing";
import { useWorkspace } from "./store/workspace";

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
// Treated as "logged in" for nav-enable purposes. Local API-key mode
// counts (development convenience); blank state shows the Connect
// flow as the only interactive control.
const isAuthed = computed(
  () => Boolean(state.auth?.connectedUser || state.auth?.allowLocalApiKeyFallback),
);

// Per-session tabs, shown above the pages not rebuilt yet
// (/single-analysis/:sessionId/*); they start with the Report.
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
// ── v1 shell ──────────────────────────────────────────────────────────────
// Signed out, the v1 shell shows the landing page (`LandingView`) in place
// of any page but the public ones, and the top bar keeps only the lockup,
// the theme toggle and "Connect with Livestorm". The URL stays, so
// connecting brings the visitor back to the page they opened (a shared
// session link keeps working). Nothing shows until the bootstrap has said
// who is signed in, so a signed-in user never sees the landing flash.
const LandingView = defineAsyncComponent(() => import("./views/LandingView.vue"));
const authReady = ref(false);
const showLanding = computed(() => showsLanding({ authReady: authReady.value, authed: isAuthed.value, path: route.path }));

// Top-bar navigation, mapped onto the existing routes until the rebuilt
// screens land: "Sessions" is the session list (and every session page),
// "Add session" is Search.
const v1NavItems = computed(() => {
  if (!isAuthed.value) return [];
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

onMounted(async () => {
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
  } finally {
    authReady.value = true;
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
  // Signed out: the home page, which is the landing page.
  router.push("/");
}

</script>

<template>
  <div class="siq-app">
    <a class="siq-skip siq-fx" href="#siq-main">Skip to content</a>
    <TopBar
      :items="v1NavItems"
      :account="v1Account"
      :can-connect="isOAuthMode"
      :local-mode="isLocalApiKeyMode"
      @connect="handleConnectClick"
      @logout="handleLogoutClick"
    />

    <main id="siq-main" class="siq-main" tabindex="-1">
      <p v-if="state.error" class="siq-alert" role="alert">{{ state.error }}</p>

      <p v-if="!authReady" class="siq-sr-only" role="status">Loading StormIQ…</p>
      <LandingView v-else-if="showLanding" :can-connect="isOAuthMode" @connect="handleConnectClick" />
      <RouterView v-else-if="isV1NativeRoute" />
      <template v-else>
        <!-- Per-session tabs, for the pages not rebuilt yet. -->
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

        <!-- Not rebuilt yet: the older view, in its own dark frame. -->
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
      </p>
    </footer>
  </div>

</template>

<style scoped>
/* ── The app shell ───────────────────────────────────────────────────────── */
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
