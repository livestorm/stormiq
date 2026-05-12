<script setup>
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import { useWorkspace } from "../store/workspace";

// Phase 4 search page. Three modes side by side in one place, instead
// of buried in the sidebar:
//   1. By session ID  — paste a known session UUID
//   2. By event ID    — paste an event UUID, pick a session from the list
//   3. By workspace   — browse events you own, filter by title / status
//
// All three are still backed by the same store actions the sidebar
// used to call — this is a UI restructure, not a behaviour change.

const {
  state,
  loadEventSessions,
  loadSessionById,
  loadWorkspaceEvents,
  loadMoreWorkspaceEvents,
  loadSessionsForSelectedWorkspaceEvent,
} = useWorkspace();
const router = useRouter();
const activeMode = ref(state.inputMode === "event" ? "event" : "session");
const eventSearchQuery = ref("");

const canUseLivestormAuth = computed(
  () => Boolean(state.apiKey || state.auth?.connectedUser),
);
const isOAuthRequired = computed(
  () => Boolean(state.auth?.oauthEnabled) && !state.auth?.connectedUser && !state.auth?.allowLocalApiKeyFallback,
);

function selectMode(mode) {
  activeMode.value = mode;
  if (mode === "session" || mode === "event") {
    state.inputMode = mode;
  }
}

// Navigate-first pattern. The user wants the destination page (with
// its built-in loader) to appear immediately on click — not after the
// whole fetch resolves. The route's watcher in App.vue would also kick
// off loadSessionById, but doing it here as well means state.loading
// .sessionFetch flips to true *before* the navigation completes, so
// SessionOverviewView never renders an empty frame between mount and
// the watcher firing. The re-entry guard inside loadSessionById stops
// the two callers from double-fetching.
function fetchBySessionId() {
  const sessionId = state.sessionId.trim();
  if (!sessionId) return;
  state.sessionId = sessionId;
  state.inputMode = "session";
  loadSessionById(sessionId).catch(() => {
    // Errors surface via state.error; the destination page already
    // renders the error banner.
  });
  router.push(`/single-analysis/${sessionId}/session-overview`);
}

async function fetchSessionsForEvent() {
  if (!state.eventId.trim()) return;
  state.inputMode = "event";
  await loadEventSessions();
}

function openEventSession() {
  const sessionId = state.selectedEventSessionId.trim();
  if (!sessionId) return;
  state.inputMode = "event";
  loadSessionById(sessionId).catch(() => {
    // Same fallback as fetchBySessionId — error banner on the page.
  });
  router.push(`/single-analysis/${sessionId}/session-overview`);
}

const filteredWorkspaceEvents = computed(() => {
  const query = eventSearchQuery.value.trim().toLowerCase();
  const events = state.workspaceEvents || [];
  if (!query) return events;
  return events.filter((event) =>
    String(event.title || event.label || "").toLowerCase().includes(query),
  );
});

function statusLabel(value) {
  return String(value || "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function statusClass(value) {
  return `status-${String(value || "unknown").toLowerCase()}`;
}

function selectWorkspaceEvent(eventId) {
  state.selectedWorkspaceEventId = eventId;
  state.eventSessions = [];
  state.loadedEventId = "";
  state.selectedEventSessionId = "";
}

async function fetchSessionsForWorkspaceEvent(eventId) {
  if (state.selectedWorkspaceEventId !== eventId) {
    selectWorkspaceEvent(eventId);
  }
  await loadSessionsForSelectedWorkspaceEvent();
}
</script>

<template>
  <section class="page-section search-view">
    <header>
      <h2>Search</h2>
      <p class="page-description">
        Fetch a session by ID, list sessions in an event, or browse every event in your workspace.
      </p>
    </header>

    <section v-if="isOAuthRequired" class="panel helper-panel">
      <h3>Connect with Livestorm to search</h3>
      <p>Search results are scoped to your Livestorm organization. Connect from the sidebar to continue.</p>
    </section>

    <template v-else>
      <div class="section-tabs search-mode-tabs">
        <button
          type="button"
          class="section-tab"
          :class="{ active: activeMode === 'session' }"
          @click="selectMode('session')"
        >
          By session ID
        </button>
        <button
          type="button"
          class="section-tab"
          :class="{ active: activeMode === 'event' }"
          @click="selectMode('event')"
        >
          By event ID
        </button>
        <button
          type="button"
          class="section-tab"
          :class="{ active: activeMode === 'workspace' }"
          @click="selectMode('workspace')"
        >
          Browse workspace
        </button>
      </div>

      <section v-if="activeMode === 'session'" class="panel">
        <h3>Fetch a session by ID</h3>
        <p class="search-mode-copy">
          Use this when a teammate shared a session UUID directly.
        </p>
        <div class="field-group search-inline-row">
          <input v-model="state.sessionId" type="text" placeholder="Session ID (UUID)" />
          <button
            class="primary"
            :disabled="state.loading.sessionFetch || !canUseLivestormAuth || !state.sessionId.trim()"
            @click="fetchBySessionId"
          >
            {{ state.loading.sessionFetch ? "Fetching…" : "Fetch session" }}
          </button>
        </div>
      </section>

      <section v-else-if="activeMode === 'event'" class="panel">
        <h3>Fetch sessions in an event</h3>
        <p class="search-mode-copy">
          Paste an event UUID to list its past sessions, then pick the one you want to analyse.
        </p>
        <div class="field-group search-inline-row">
          <input v-model="state.eventId" type="text" placeholder="Event ID (UUID)" />
          <button
            class="primary"
            :disabled="state.loading.eventSessions || !canUseLivestormAuth || !state.eventId.trim()"
            @click="fetchSessionsForEvent"
          >
            {{ state.loading.eventSessions ? "Fetching sessions…" : "List sessions" }}
          </button>
        </div>

        <div v-if="state.eventSessions.length" class="field-group">
          <label class="field-label">Sessions for event {{ state.eventId }}</label>
          <select v-model="state.selectedEventSessionId">
            <option value="">Select a session…</option>
            <option v-for="session in state.eventSessions" :key="session.id" :value="session.id">
              {{ session.label }}
            </option>
          </select>
          <button
            class="primary"
            :disabled="state.loading.sessionFetch || !canUseLivestormAuth || !state.selectedEventSessionId.trim()"
            @click="openEventSession"
          >
            {{ state.loading.sessionFetch ? "Fetching…" : "Open session" }}
          </button>
        </div>
      </section>

      <section v-else class="panel">
        <h3>Browse workspace events</h3>
        <p class="search-mode-copy">
          Filter your organization's events by title or status, then pick one to open its sessions.
        </p>

        <div class="field-group search-filters-row">
          <input v-model="state.workspaceEventsTitle" type="text" placeholder="Filter by event title" />
          <select v-model="state.workspaceEventsStatus">
            <option value="">All statuses</option>
            <option value="live">Live</option>
            <option value="upcoming">Upcoming</option>
            <option value="on_demand">On demand</option>
            <option value="ended">Ended</option>
            <option value="not_started">Not started</option>
            <option value="draft">Draft</option>
            <option value="not_scheduled">Not scheduled</option>
          </select>
          <button
            class="primary"
            :disabled="state.loading.workspaceEvents || !canUseLivestormAuth"
            @click="loadWorkspaceEvents"
          >
            {{ state.loading.workspaceEvents ? "Fetching events…" : "Fetch events" }}
          </button>
        </div>

        <div v-if="state.workspaceEvents.length" class="field-group">
          <input v-model="eventSearchQuery" type="text" placeholder="Search loaded events" />
          <div class="search-events-grid">
            <article
              v-for="event in filteredWorkspaceEvents"
              :key="event.id"
              class="event-card"
              :class="{ selected: state.selectedWorkspaceEventId === event.id, expanded: state.selectedWorkspaceEventId === event.id }"
            >
              <button type="button" class="event-card-summary" @click="selectWorkspaceEvent(event.id)">
                <div class="event-card-header">
                  <h4 class="event-card-title">{{ event.title || event.label }}</h4>
                  <span class="event-status-pill" :class="statusClass(event.scheduling_status)">
                    {{ statusLabel(event.scheduling_status || "") }}
                  </span>
                </div>
                <div class="event-card-meta">
                  <span>Sessions: {{ event.sessions_count ?? 0 }}</span>
                </div>
              </button>
              <div v-if="state.selectedWorkspaceEventId === event.id" class="event-card-session-picker">
                <p class="event-card-id">{{ event.id }}</p>
                <button
                  class="ghost-link-button"
                  type="button"
                  :disabled="state.loading.eventSessions || !canUseLivestormAuth"
                  @click="fetchSessionsForWorkspaceEvent(event.id)"
                >
                  {{
                    state.loading.eventSessions && state.selectedWorkspaceEventId === event.id
                      ? "Fetching sessions…"
                      : "Open event"
                  }}
                </button>
              </div>
            </article>
          </div>

          <div v-if="state.workspaceEventsNextPage !== null && state.workspaceEventsNextPage !== undefined" class="action-row">
            <button
              type="button"
              class="ghost-link-button"
              :disabled="state.loading.workspaceEvents"
              @click="loadMoreWorkspaceEvents"
            >
              {{ state.loading.workspaceEvents ? "Loading more…" : "Load more events" }}
            </button>
          </div>
        </div>
      </section>
    </template>
  </section>
</template>

<style scoped>
.search-mode-copy {
  margin-top: 0;
  margin-bottom: 12px;
  color: #5d6d79;
  font-size: 14px;
}

.search-inline-row {
  display: flex;
  gap: 8px;
  align-items: stretch;
  flex-wrap: nowrap;
}

.search-inline-row > input,
.search-inline-row > select {
  flex: 1 1 auto;
  min-width: 0;
}

.search-inline-row > button {
  flex: 0 0 auto;
  white-space: nowrap;
}

.search-filters-row {
  display: grid;
  grid-template-columns: 1fr 200px auto;
  gap: 8px;
  align-items: stretch;
}

.search-mode-tabs {
  margin-bottom: 12px;
}

.search-events-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
}

.field-label {
  font-size: 13px;
  font-weight: 500;
  color: #4d5b66;
  margin-bottom: 4px;
}
</style>
