<script setup>
import { computed, ref } from "vue";
import { useWorkspace } from "../store/workspace";

const { state, loadMoreWorkspaceEvents, loadSessionsForSelectedWorkspaceEvent } = useWorkspace();
const localSearch = ref("");

const hasEvents = computed(() => state.workspaceEvents.length > 0);
const canLoadMore = computed(
  () => state.workspaceEventsNextPage !== null && state.workspaceEventsNextPage !== undefined
);
const canUseLivestormAuth = computed(() => Boolean(state.apiKey || state.auth?.connectedUser));
const isInitialLoading = computed(() => state.loading.workspaceEvents && !state.workspaceEvents.length);
const filteredEvents = computed(() => {
  const query = localSearch.value.trim().toLowerCase();
  if (!query) return state.workspaceEvents;
  return state.workspaceEvents.filter((event) =>
    String(event.title || event.label || "").toLowerCase().includes(query)
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

function handleToggleEvent(eventId) {
  if (state.selectedWorkspaceEventId === eventId) return;
  state.selectedWorkspaceEventId = eventId;
  state.eventSessions = [];
  state.loadedEventId = "";
  state.selectedEventSessionId = "";
}

async function handleFetchSessions(eventId) {
  if (state.selectedWorkspaceEventId !== eventId) {
    handleToggleEvent(eventId);
  }
  await loadSessionsForSelectedWorkspaceEvent();
}
</script>

<template>
  <section v-if="isInitialLoading" class="panel loading-panel">
    <div class="loading-indicator" aria-hidden="true"></div>
    <div>
      <h3 class="loading-title">Fetching Events</h3>
      <p class="loading-copy">Loading the first page of events from your Livestorm workspace.</p>
    </div>
  </section>

  <section v-else-if="hasEvents" class="panel">
    <div class="events-toolbar">
      <div>
        <h2 class="panel-title">Events</h2>
        <p class="panel-subtitle">
          Browse the fetched events, choose one, then load its sessions before fetching the session data.
        </p>
      </div>
    </div>

    <div class="events-meta">
      <span>{{ filteredEvents.length }} of {{ state.workspaceEvents.length }} event{{ state.workspaceEvents.length === 1 ? "" : "s" }} shown</span>
      <span v-if="state.workspaceEventsTitle.trim()">Title filter: "{{ state.workspaceEventsTitle.trim() }}"</span>
      <span v-if="state.workspaceEventsStatus.trim()">Status: {{ statusLabel(state.workspaceEventsStatus) }}</span>
    </div>

    <div class="field-group events-local-search">
      <input v-model="localSearch" type="text" placeholder="Search loaded events by title" />
    </div>

    <div class="events-grid">
      <article
        v-for="event in filteredEvents"
        :key="event.id"
        class="event-card"
        :class="{ selected: state.selectedWorkspaceEventId === event.id, expanded: state.selectedWorkspaceEventId === event.id }"
      >
        <button type="button" class="event-card-summary" @click="handleToggleEvent(event.id)">
          <div class="event-card-header">
            <div class="event-card-title-wrap">
              <h3 class="event-card-title">{{ event.title || event.label }}</h3>
            </div>
            <span class="event-status-pill" :class="statusClass(event.scheduling_status)">
              {{ statusLabel(event.scheduling_status || "") }}
            </span>
          </div>

          <div class="event-card-meta">
            <span>Sessions: {{ event.sessions_count ?? 0 }}</span>
          </div>
        </button>

        <div v-if="state.selectedWorkspaceEventId === event.id" class="event-card-session-picker">
          <div>
            <p class="event-card-id">{{ event.id }}</p>
            <p v-if="event.updated_label" class="field-hint">Updated: {{ event.updated_label }}</p>
          </div>
          <button
            type="button"
            class="ghost-link-button"
            :disabled="state.loading.eventSessions || !canUseLivestormAuth"
            @click="handleFetchSessions(event.id)"
          >
            {{
              state.loading.eventSessions && state.selectedWorkspaceEventId === event.id
                ? "Fetching sessions..."
                : "Fetch sessions"
            }}
          </button>
        </div>
      </article>
    </div>

    <p v-if="!filteredEvents.length" class="field-hint">
      No loaded events match that title search yet.
    </p>

    <div v-if="canLoadMore" class="events-load-more">
      <button
        type="button"
        class="ghost-link-button"
        :disabled="state.loading.workspaceEvents"
        @click="loadMoreWorkspaceEvents"
      >
        {{ state.loading.workspaceEvents ? "Loading more..." : "Load more" }}
      </button>
    </div>
  </section>

  <section v-else class="panel helper-panel">
    <h2 class="panel-title">Events</h2>
    <p class="helper-text">
      Fetch events from the sidebar to populate this page. You can filter by title or status first, then browse the
      results here as cards.
    </p>
  </section>
</template>
