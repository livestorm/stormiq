<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink } from "vue-router";
import Card from "../components/ui/Card.vue";
import Icon from "../components/ui/Icon.vue";
import StatCard from "../components/ui/StatCard.vue";
import StatusWord from "../components/ui/StatusWord.vue";
import UiButton from "../components/ui/UiButton.vue";
import { SORTS, eventOptions, sessionCounts, sessionRow, visibleSessions } from "../sessions/list";
import { useWorkspace } from "../store/workspace";

// The v1 Sessions page: every session the organization has fetched, as one
// index table. The list comes from the store (`loadWorkspaceSessions`,
// `GET /api/workspace-sessions`); `sessions/list.js` filters, sorts and
// words it. A row opens the session's Report.

const { state, loadWorkspaceSessions } = useWorkspace();

const STALE_AFTER_MS = 60 * 1000;
function load() {
  if (!state.auth.connectedUser && !state.auth.allowLocalApiKeyFallback) return;
  const stale = Date.now() - (state.workspaceSessionsLoadedAt || 0) > STALE_AFTER_MS;
  if (!state.workspaceSessions.length || stale) loadWorkspaceSessions().catch(() => {});
}
onMounted(load);
watch(() => [state.auth.connectedUser, state.auth.allowLocalApiKeyFallback], load);

const query = ref("");
const event = ref("");
const sort = ref("sessionDesc");

const sessions = computed(() => state.workspaceSessions || []);
const loading = computed(() => state.loading.workspaceSessions && !sessions.value.length);
const counts = computed(() => sessionCounts(sessions.value));
const events = computed(() => eventOptions(sessions.value));
const rows = computed(() =>
  visibleSessions(sessions.value, { query: query.value, event: event.value, sort: sort.value }).map((session) =>
    sessionRow(session),
  ),
);
const filtered = computed(() => Boolean(query.value.trim() || event.value));
const shownLabel = computed(() => {
  const total = counts.value.sessions;
  const noun = total === 1 ? "session" : "sessions";
  return filtered.value ? `${rows.value.length} of ${total} ${noun}` : `${total} ${noun}`;
});

function clearFilters() {
  query.value = "";
  event.value = "";
}
</script>

<template>
  <div class="s-page">
    <header class="s-head">
      <h1 class="s-title">Sessions</h1>
      <UiButton variant="primary" icon="plus" to="/search">Add session</UiButton>
    </header>

    <p v-if="loading" class="s-status" role="status">Loading your sessions…</p>

    <Card v-else-if="!sessions.length" class="s-empty" padding="hero">
      <h2 class="s-empty-title">No sessions yet</h2>
      <p class="s-empty-text">Add a finished Livestorm session to see its report here. Your teammates' sessions show up too.</p>
      <UiButton variant="secondary" icon="plus" to="/search">Add your first session</UiButton>
    </Card>

    <template v-else>
      <section class="s-counts" aria-label="Your organization's sessions">
        <StatCard icon="calendar" label="Sessions" :value="String(counts.sessions)" />
        <StatCard icon="file-text" label="With a transcript" :value="String(counts.transcripts)" />
        <StatCard icon="list" label="Events" :value="String(counts.events)" />
      </section>

      <Card padding="flush" class="s-index">
        <div class="s-filters" role="search">
          <label class="s-field s-search">
            <span class="siq-sr-only">Search sessions</span>
            <Icon name="search" :size="16" class="s-field-icon" />
            <input v-model="query" type="search" class="s-input" placeholder="Search by title, event or teammate" />
          </label>
          <label v-if="events.length > 1" class="s-field">
            <span class="siq-sr-only">Event</span>
            <select v-model="event" class="s-input s-select">
              <option value="">All events</option>
              <option v-for="option in events" :key="option.value" :value="option.value">{{ option.label }}</option>
            </select>
            <Icon name="chevron-down" :size="16" class="s-select-icon" />
          </label>
          <label class="s-field">
            <span class="siq-sr-only">Sort</span>
            <select v-model="sort" class="s-input s-select">
              <option v-for="option in SORTS" :key="option.value" :value="option.value">{{ option.label }}</option>
            </select>
            <Icon name="chevron-down" :size="16" class="s-select-icon" />
          </label>
          <p class="s-shown" role="status">{{ shownLabel }}</p>
        </div>

        <table v-if="rows.length" class="s-table">
          <caption class="siq-sr-only">Your organization's sessions</caption>
          <thead>
            <tr>
              <th scope="col">Date</th>
              <th scope="col">Session</th>
              <th scope="col" class="s-num">Attendees</th>
              <th scope="col">Transcript</th>
              <th scope="col">Added by</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="row.id" class="s-row">
              <td class="s-date">{{ row.day }}</td>
              <td class="s-session">
                <!-- The title's link covers the whole row (::after). -->
                <RouterLink :to="row.href" class="s-link siq-fx">{{ row.title }}</RouterLink>
                <span class="s-meta">
                  <span v-if="row.subtitle">{{ row.subtitle }}</span>
                  <span v-if="row.subtitle && row.length" aria-hidden="true"> · </span>
                  <span v-if="row.length">{{ row.length }}</span>
                </span>
              </td>
              <td class="s-num">
                <span class="s-attendees">{{ row.attendees }}</span>
                <span v-if="row.registrants" class="s-meta">{{ row.registrants }}</span>
              </td>
              <td class="s-transcript">
                <StatusWord v-if="row.transcript === 'ready'" status="measured" label="Ready" />
                <span v-else class="s-meta">Not yet</span>
              </td>
              <td class="s-added">{{ row.addedBy || "—" }}</td>
            </tr>
          </tbody>
        </table>
        <div v-else class="s-none">
          <p class="s-empty-text">No session matches.</p>
          <UiButton variant="ghost" icon="x" @click="clearFilters">Clear the search</UiButton>
        </div>
      </Card>
      <p class="s-note">Attendees and registrants are Livestorm's counts, your team included. Each session's Report counts the audience alone.</p>
    </template>
  </div>
</template>

<style scoped>
.s-page {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-6);
}

.s-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--siq-space-4);
  flex-wrap: wrap;
}

.s-title {
  margin: 0;
  font-size: var(--siq-text-display);
  line-height: var(--siq-text-display-lh);
  font-weight: var(--siq-weight-regular);
  letter-spacing: -0.015em;
  color: var(--siq-ink);
}

.s-status,
.s-note,
.s-shown {
  margin: 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
}

.s-empty {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--siq-space-3);
}

.s-empty-title {
  margin: 0;
  font-size: var(--siq-text-section);
  line-height: var(--siq-text-section-lh);
  font-weight: var(--siq-weight-regular);
  color: var(--siq-ink);
}

.s-empty-text {
  margin: 0;
  max-width: 680px;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-ink-2);
}

.s-counts {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--siq-space-4);
}

/* ── Filters ── */
.s-filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--siq-space-3);
  padding: var(--siq-space-4) var(--siq-space-6);
  box-shadow: inset 0 -1px 0 var(--siq-hairline);
}

.s-field {
  position: relative;
  display: inline-flex;
  align-items: center;
}

.s-search {
  flex: 1 1 18rem;
}

/* v1 controls set their own font, colours, border and height: the classic
   element styles still load under v1. */
.s-input {
  width: 100%;
  height: var(--siq-target);
  margin: 0;
  padding: 0 var(--siq-space-3);
  border: 1px solid var(--siq-border);
  border-radius: var(--siq-radius-control);
  background: var(--siq-input);
  color: var(--siq-ink);
  font: inherit;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  box-shadow: none;
  transition-property: border-color, box-shadow;
  transition-duration: var(--siq-duration);
  transition-timing-function: var(--siq-ease);
}

.s-input:hover {
  border-color: var(--siq-border-hover);
}

.s-input:focus-visible {
  outline: 2px solid var(--siq-focus);
  outline-offset: 1px;
}

.s-search .s-input {
  padding-left: 36px;
}

.s-input::placeholder {
  color: var(--siq-muted);
}

.s-field-icon,
.s-select-icon {
  position: absolute;
  color: var(--siq-muted);
  pointer-events: none;
}

.s-field-icon {
  left: var(--siq-space-3);
}

.s-select {
  appearance: none;
  padding-right: 36px;
  min-width: 12rem;
  cursor: pointer;
}

.s-select-icon {
  right: var(--siq-space-3);
}

.s-shown {
  margin-left: auto;
}

/* ── The index ── */
.s-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-ink);
}

.s-table th,
.s-table td {
  padding: var(--siq-space-3) var(--siq-space-4);
  text-align: left;
  vertical-align: middle;
  box-shadow: inset 0 -1px 0 var(--siq-hairline-row);
}

.s-table th:first-child,
.s-table td:first-child {
  padding-left: var(--siq-space-6);
}

.s-table th:last-child,
.s-table td:last-child {
  padding-right: var(--siq-space-6);
}

.s-table thead th {
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-muted);
  box-shadow: inset 0 -1px 0 var(--siq-hairline-head);
}

.s-table tbody tr:last-child td {
  box-shadow: none;
}

.s-row {
  position: relative;
  transition-property: background-color;
  transition-duration: var(--siq-duration);
  transition-timing-function: var(--siq-ease);
}

.s-row:hover,
.s-row:focus-within {
  background: var(--siq-card-hover);
}

.s-table .s-num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.s-date {
  white-space: nowrap;
  color: var(--siq-ink-2);
  font-variant-numeric: tabular-nums;
}

.s-session {
  min-width: 14rem;
}

.s-link {
  display: block;
  color: var(--siq-ink);
  font-weight: var(--siq-weight-medium);
  text-decoration: none;
  border-radius: var(--siq-radius-inline);
}

/* The whole row opens the session. */
.s-link::after {
  content: "";
  position: absolute;
  inset: 0;
}

.s-link:hover {
  color: var(--siq-accent);
}

.s-link.siq-fx:focus-visible {
  outline: none;
}

.s-link.siq-fx:focus-visible::after {
  outline: 2px solid var(--siq-focus);
  outline-offset: -3px;
  border-radius: var(--siq-radius-inline);
}

.s-meta {
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
}

.s-num .s-meta {
  display: block;
}

.s-added {
  color: var(--siq-ink-2);
}

.s-none {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--siq-space-2);
  padding: var(--siq-space-6);
}

/* On a phone each row becomes a small block: the title first, then the
   date, attendees and transcript on one line. */
@media (max-width: 720px) {
  .s-title {
    font-size: var(--siq-text-headline);
    line-height: var(--siq-text-headline-lh);
  }
  .s-counts {
    gap: var(--siq-space-2);
  }
  .s-counts :deep(.siq-stat) {
    padding: var(--siq-space-4);
  }
  .s-counts :deep(.siq-stat-head .siq-tile) {
    display: none;
  }
  .s-filters {
    padding: var(--siq-space-4);
  }
  .s-field {
    flex: 1 1 100%;
  }
  .s-select {
    min-width: 0;
  }
  .s-shown {
    margin-left: 0;
  }
  .s-table thead {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
  }
  .s-table,
  .s-table tbody {
    display: block;
  }
  .s-row {
    display: grid;
    grid-template-columns: auto auto 1fr;
    grid-template-areas:
      "session session session"
      "date num transcript";
    align-items: baseline;
    gap: var(--siq-space-1) var(--siq-space-3);
    padding: var(--siq-space-3) var(--siq-space-4);
    box-shadow: inset 0 -1px 0 var(--siq-hairline-row);
  }
  .s-table td,
  .s-table td:first-child,
  .s-table td:last-child {
    padding: 0;
    box-shadow: none;
  }
  .s-session {
    grid-area: session;
    min-width: 0;
  }
  .s-date {
    grid-area: date;
  }
  .s-table .s-num {
    grid-area: num;
    text-align: left;
  }
  .s-transcript {
    grid-area: transcript;
  }
  .s-num .s-meta {
    display: inline;
    margin-left: var(--siq-space-1);
  }
  .s-added {
    display: none;
  }
}

@media (forced-colors: active) {
  .s-table th,
  .s-table td,
  .s-filters {
    border-bottom: 1px solid CanvasText;
  }
}

@media (prefers-reduced-motion: reduce) {
  .s-row,
  .s-input {
    transition: none;
  }
}
</style>
