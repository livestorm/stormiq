<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { RouterLink, RouterView, useRoute, useRouter } from "vue-router";
import { api } from "./api";
import FetchSessionForm from "./components/FetchSessionForm.vue";
import { useWorkspace } from "./store/workspace";

const {
  state,
  applyBootstrap,
  fetchSessionData,
  hasTranscriptData,
  isTranscriptLoading,
  isTranscriptUnavailable,
  loadWorkspaceEvents,
  resetWorkspace,
} = useWorkspace();
const route = useRoute();
const router = useRouter();
const logoUrl = "/brand-assets/icons/Icon-Livestorm-Tertiary-Light.png";
const hasEvents = computed(() => state.workspaceEvents.length > 0);
const sidebarCollapsed = ref(false);
const isCompactViewport = ref(false);
const mainNavOpen = ref(true);
const COMPACT_BREAKPOINT = 1100;

const navItems = [
  { to: "/events", label: "Events", key: "events" },
  { to: "/session-overview", label: "Session Overview", key: "session" },
  { to: "/transcript", label: "Transcript", key: "transcript" },
  { to: "/chat-questions", label: "Chat & Questions", key: "chat" },
  { to: "/analysis", label: "Analysis", key: "analysis" },
  { to: "/content-repurposing", label: "Repurposing", key: "repurposing" },
  { to: "/smart-recap", label: "Smart Recap", key: "recap" },
];

const navStateByKey = computed(() => {
  const hasWorkspace = Boolean(state.workspace);
  const transcriptReady = hasTranscriptData.value;
  const transcriptLoading = isTranscriptLoading.value;
  const transcriptUnavailable = isTranscriptUnavailable.value;
  const isFreshSessionFetch =
    state.loading.sessionFetch &&
    (
      (state.inputMode === "session" && Boolean(state.sessionId.trim())) ||
      (state.inputMode === "event" && Boolean(state.selectedEventSessionId.trim()))
    );

  if (isFreshSessionFetch) {
    return {
      events: { disabled: true, loading: true, ready: false },
      session: { disabled: true, loading: true, ready: false },
      chat: { disabled: true, loading: true, ready: false },
      transcript: { disabled: true, loading: true, ready: false },
      analysis: { disabled: true, loading: true, ready: false },
      repurposing: { disabled: true, loading: true, ready: false },
      recap: { disabled: true, loading: true, ready: false },
    };
  }

  return {
    events: { disabled: !hasEvents.value, loading: state.loading.workspaceEvents, ready: hasEvents.value },
    session: { disabled: !hasWorkspace, loading: false, ready: hasWorkspace },
    chat: { disabled: !hasWorkspace, loading: false, ready: hasWorkspace },
    transcript: { disabled: !hasWorkspace || transcriptUnavailable, loading: transcriptLoading, ready: transcriptReady, unavailable: transcriptUnavailable },
    analysis: { disabled: !transcriptReady || transcriptUnavailable, loading: transcriptLoading, ready: transcriptReady, unavailable: transcriptUnavailable },
    repurposing: { disabled: !transcriptReady || transcriptUnavailable, loading: transcriptLoading, ready: transcriptReady, unavailable: transcriptUnavailable },
    recap: { disabled: !transcriptReady || transcriptUnavailable, loading: transcriptLoading, ready: transcriptReady, unavailable: transcriptUnavailable },
  };
});

function getNavMeta(item) {
  return navStateByKey.value[item.key] || { disabled: false, loading: false, ready: false };
}

function syncViewportMode() {
  if (typeof window === "undefined") return;
  const nextCompact = window.innerWidth <= COMPACT_BREAKPOINT;
  isCompactViewport.value = nextCompact;
  sidebarCollapsed.value = nextCompact;
  mainNavOpen.value = !nextCompact;
}

onMounted(async () => {
  syncViewportMode();
  window.addEventListener("resize", syncViewportMode);
  try {
    const bootstrap = await api.bootstrap();
    applyBootstrap(bootstrap);
    if (route.path === "/auth/callback") {
      await router.replace("/");
    }
  } catch (_error) {
    // Ignore bootstrap failures so manual entry still works without friction.
  }
});

onBeforeUnmount(() => {
  if (typeof window !== "undefined") {
    window.removeEventListener("resize", syncViewportMode);
  }
});

function handleConnectClick() {
  const returnTo = route.path || "/";
  window.location.href = `/api/auth/livestorm/start?returnTo=${encodeURIComponent(returnTo)}`;
}

async function handleLogoutClick() {
  try {
    await api.logout();
  } catch (_error) {
    // Ignore logout failures and clear the local state anyway.
  }
  state.auth.connectedUser = null;
  state.apiKey = "";
  resetWorkspace();
  router.push("/");
}

async function handleFetchClick() {
  try {
    if (
      (state.inputMode === "session" && state.sessionId.trim()) ||
      (state.inputMode === "event" && state.selectedEventSessionId.trim())
    ) {
      router.push("/session-overview");
    }
    await fetchSessionData(false);
  } catch (_error) {
    // The workspace store already surfaces a friendly message in the sidebar.
  }
}

async function handleFetchEventsClick() {
  try {
    router.push("/events");
    await loadWorkspaceEvents();
  } catch (_error) {
    // The workspace store already surfaces a friendly message in the sidebar.
  }
}

function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value;
}

function toggleMainNav() {
  mainNavOpen.value = !mainNavOpen.value;
}

function handleNavClick(navigate) {
  navigate();
  if (isCompactViewport.value) {
    mainNavOpen.value = false;
  }
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
    <div
      v-if="isCompactViewport && mainNavOpen"
      class="nav-backdrop"
      aria-hidden="true"
      @click="mainNavOpen = false"
    ></div>

    <aside class="sidebar" :class="{ collapsed: sidebarCollapsed, compact: isCompactViewport }">
      <div class="sidebar-brand" :class="{ collapsed: sidebarCollapsed }">
        <button
          type="button"
          class="brand-logo-button"
          :aria-label="sidebarCollapsed ? 'Open navigation and controls' : 'Close navigation and controls'"
          @click="toggleSidebar()"
        >
          <img :src="logoUrl" alt="Livestorm" class="brand-logo" />
        </button>
        <div v-if="!sidebarCollapsed" class="brand-copy">
          <h1>StormIQ</h1>
        </div>
        <button
          v-if="isCompactViewport"
          type="button"
          class="sidebar-toggle sidebar-toggle-inside"
          :aria-expanded="String(!sidebarCollapsed)"
          :aria-label="sidebarCollapsed ? 'Open navigation and controls' : 'Close navigation and controls'"
          @click="toggleSidebar"
        >
          <span aria-hidden="true">{{ sidebarCollapsed ? "☰" : "✕" }}</span>
        </button>
      </div>

      <template v-if="!sidebarCollapsed">
        <FetchSessionForm
          :state="state"
          @fetch="handleFetchClick"
          @fetch-events="handleFetchEventsClick"
          @connect="handleConnectClick"
          @logout="handleLogoutClick"
        />

        <p v-if="state.error" class="error-text">{{ state.error }}</p>

        <div class="sidebar-beta-notice">
          <p class="sidebar-beta-title">Beta notice</p>
          <p class="sidebar-beta-copy">
            This tool is an early-access helper and not an official Livestorm process. Review outputs before relying on
            them.
          </p>
          <RouterLink to="/beta-info" class="sidebar-beta-link">Read more</RouterLink>
        </div>
      </template>
    </aside>

    <main class="main-content">
      <div v-if="isCompactViewport" class="compact-topbar">
        <button
          type="button"
          class="compact-topbar-toggle"
          :aria-expanded="String(mainNavOpen)"
          :aria-label="mainNavOpen ? 'Close section navigation' : 'Open section navigation'"
          @click="toggleMainNav"
        >
          <span aria-hidden="true">{{ mainNavOpen ? "✕" : "☰" }}</span>
        </button>
        <button
          type="button"
          class="compact-topbar-logo-button"
          :aria-expanded="String(!sidebarCollapsed)"
          :aria-label="sidebarCollapsed ? 'Open navigation and controls' : 'Close navigation and controls'"
          @click="toggleSidebar"
        >
          <img :src="logoUrl" alt="StormIQ" class="compact-topbar-logo" />
        </button>
      </div>

      <nav class="top-nav" :class="{ 'top-nav-compact-open': isCompactViewport && mainNavOpen }">
        <RouterLink v-for="item in navItems" :key="item.to" :to="item.to" custom v-slot="{ navigate }">
          <button
            type="button"
            class="top-nav-item"
            :class="{
              'router-link-active': route.path === item.to,
              disabled: getNavMeta(item).disabled,
              loading: getNavMeta(item).loading,
            }"
            :disabled="getNavMeta(item).disabled"
            @click="handleNavClick(navigate)"
          >
            <span class="top-nav-item-text">{{ item.label }}</span>
            <span v-if="getNavMeta(item).loading" class="top-nav-status top-nav-status-loading" aria-hidden="true"></span>
            <span v-else-if="getNavMeta(item).unavailable" class="top-nav-status top-nav-status-unavailable" aria-hidden="true"></span>
            <span v-else-if="getNavMeta(item).ready" class="top-nav-status top-nav-status-ready" aria-hidden="true"></span>
          </button>
        </RouterLink>
      </nav>
      <RouterView />
    </main>
  </div>
</template>
