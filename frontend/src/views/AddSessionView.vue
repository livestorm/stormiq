<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink, useRouter } from "vue-router";
import Card from "../components/ui/Card.vue";
import CardHeader from "../components/ui/CardHeader.vue";
import Collapsible from "../components/ui/Collapsible.vue";
import Icon from "../components/ui/Icon.vue";
import StatusWord from "../components/ui/StatusWord.vue";
import UiButton from "../components/ui/UiButton.vue";
import { EVENT_STATUSES, addedSessionIds, eventRow, finishedLine, idFromInput, sessionChoice } from "../sessions/add";
import { useWorkspace } from "../store/workspace";

// Add session: pick a finished session from one of the organization's
// Livestorm events, or paste a session's or an event's link or ID. Adding
// fetches the session (`loadSessionById`: the cache, or Livestorm, then the
// transcript job) and opens its Report as soon as the session is saved; the
// transcript goes on in the background. Errors show in the shell's alert
// (`state.error`). Wording: `sessions/add.js`.

const {
  state,
  loadEventSessions,
  loadMoreEventSessions,
  loadSessionById,
  loadWorkspaceEvents,
  loadMoreWorkspaceEvents,
  loadWorkspaceSessions,
} = useWorkspace();
const router = useRouter();

const signedIn = computed(() => Boolean(state.apiKey || state.auth.connectedUser));

// ── Events ──
const titleQuery = ref(state.workspaceEventsTitle || "");
const status = ref(state.workspaceEventsStatus || "");
const searched = ref(false);

function findEvents() {
  if (!signedIn.value) return;
  state.workspaceEventsTitle = titleQuery.value.trim();
  state.workspaceEventsStatus = status.value;
  searched.value = true;
  loadWorkspaceEvents().catch(() => {});
}

onMounted(() => {
  if (!signedIn.value) return;
  if (!state.workspaceEvents.length) findEvents();
  else searched.value = true;
  if (!state.workspaceSessions.length) loadWorkspaceSessions({ silent: true }).catch(() => {});
});

const events = computed(() => (state.workspaceEvents || []).map(eventRow));
const loadingEvents = computed(() => state.loading.workspaceEvents && !events.value.length);
const hasMore = computed(() => state.workspaceEventsNextPage !== null && state.workspaceEventsNextPage !== undefined);
const eventsLabel = computed(() => {
  const count = events.value.length;
  return `${count} ${count === 1 ? "event" : "events"}${hasMore.value ? " so far" : ""}`;
});

// ── One event's sessions ──
// An event opened from a pasted ID that isn't in the list shows first.
const openEventId = ref("");
const pastedEvent = ref(null);
const shownEvents = computed(() =>
  pastedEvent.value && !events.value.some((event) => event.id === pastedEvent.value.id)
    ? [pastedEvent.value, ...events.value]
    : events.value,
);

function openEvent(id) {
  if (openEventId.value === id) {
    openEventId.value = "";
    return;
  }
  openEventId.value = id;
  state.inputMode = "event";
  state.eventId = id;
  state.selectedEventSessionId = "";
  loadEventSessions().catch(() => {});
}

const added = computed(() => addedSessionIds(state.workspaceSessions || []));
const sessionsReady = computed(
  () => Boolean(openEventId.value) && state.loadedEventId === openEventId.value && !state.loading.eventSessions,
);
const sessions = computed(() =>
  sessionsReady.value ? (state.eventSessions || []).map((option) => sessionChoice(option, added.value)) : [],
);

// ── Adding ──
const addingId = ref("");
// The session whose fetch failed, so its row says why (the shell's alert
// says it too, at the top of the page).
const failedId = ref("");

function open(id) {
  router.push(`/sessions/${encodeURIComponent(id)}`);
}

function add(id) {
  if (!id || addingId.value) return;
  addingId.value = id;
  failedId.value = "";
  loadSessionById(id).catch(() => {
    if (addingId.value !== id) return;
    addingId.value = "";
    failedId.value = id;
  });
  // Already on screen in the store: open it now.
  if (state.workspace?.sessionId === id) open(id);
}

// The Report reads the saved session, so open it once the session is saved.
watch(
  () => state.workspace?.sessionId,
  (id) => {
    if (id && id === addingId.value) open(id);
  },
);

// ── Pasted IDs ──
const sessionInput = ref("");
const eventInput = ref("");
const sessionProblem = ref("");
const eventProblem = ref("");

function problemText(problem, noun) {
  if (problem === "many") return `That link holds more than one ID. Paste the ${noun}'s ID on its own.`;
  return problem ? `Paste ${noun === "event" ? "an" : "a"} ${noun} link or ID.` : "";
}

function addPasted() {
  const { id, problem } = idFromInput(sessionInput.value);
  sessionProblem.value = problemText(problem, "session");
  if (id) add(id);
}

function listPasted() {
  const { id, problem } = idFromInput(eventInput.value);
  eventProblem.value = problemText(problem, "event");
  if (!id) return;
  pastedEvent.value = { id, title: "The event you pasted", status: "", sessions: "" };
  openEventId.value = "";
  openEvent(id);
}
</script>

<template>
  <div class="a-page">
    <nav class="a-crumbs" aria-label="Breadcrumb">
      <RouterLink class="a-crumb siq-fx" to="/sessions">Sessions</RouterLink>
      <Icon name="chevron-right" :size="14" />
      <span aria-current="page">Add session</span>
    </nav>

    <header class="a-head">
      <h1 class="a-title">Add session</h1>
      <p class="a-lead">
        Pick a finished session from your organization's Livestorm events. StormIQ fetches its chat, questions and
        recording, starts its transcript, and opens its Report.
      </p>
    </header>

    <Card padding="flush" class="a-events">
      <div class="a-events-head">
        <CardHeader icon="calendar" title="Your events" subtitle="Open an event to see its finished sessions." />
        <form class="a-filters" role="search" @submit.prevent="findEvents">
          <label class="a-field a-search">
            <span class="siq-sr-only">Event title</span>
            <Icon name="search" :size="16" class="a-field-icon" />
            <input v-model="titleQuery" type="search" class="a-input" placeholder="Search events by title" />
          </label>
          <label class="a-field">
            <span class="siq-sr-only">Event status</span>
            <select v-model="status" class="a-input a-select" @change="findEvents">
              <option v-for="option in EVENT_STATUSES" :key="option.value" :value="option.value">{{ option.label }}</option>
            </select>
            <Icon name="chevron-down" :size="16" class="a-select-icon" />
          </label>
          <UiButton type="submit" variant="secondary" icon="search" :disabled="state.loading.workspaceEvents">Find events</UiButton>
          <p v-if="events.length" class="a-muted a-count" role="status">{{ eventsLabel }}</p>
        </form>
      </div>

      <p v-if="loadingEvents" class="a-muted a-pad" role="status">Loading your events…</p>
      <p v-else-if="searched && !shownEvents.length" class="a-muted a-pad">
        No event matches. Try another title or status, or paste a link below.
      </p>

      <ul v-if="shownEvents.length" class="a-list">
        <li v-for="event in shownEvents" :key="event.id" class="a-item">
          <button
            type="button"
            class="a-event siq-fx"
            :aria-expanded="openEventId === event.id ? 'true' : 'false'"
            :aria-controls="openEventId === event.id ? `a-sessions-${event.id}` : undefined"
            @click="openEvent(event.id)"
          >
            <span class="a-event-text">
              <span class="a-event-title">{{ event.title }}</span>
              <span class="a-muted">{{ [event.status, event.sessions].filter(Boolean).join(" · ") }}</span>
            </span>
            <Icon :name="openEventId === event.id ? 'chevron-up' : 'chevron-down'" :size="18" class="a-chevron" />
          </button>

          <div v-if="openEventId === event.id" :id="`a-sessions-${event.id}`" class="a-sessions">
            <p v-if="!sessionsReady" class="a-muted" role="status">Loading the event's sessions…</p>
            <p v-else-if="!sessions.length" class="a-muted">
              This event has no finished session yet{{ event.count ? `: its ${event.sessions} are still to come, never started, cancelled or drafts` : "" }}.
            </p>
            <p v-else-if="finishedLine(state.eventSessionsTotal, event.count)" class="a-muted a-count">
              {{ finishedLine(state.eventSessionsTotal, event.count) }}
            </p>
            <ul v-else class="a-session-list" :aria-label="`Finished sessions of ${event.title}`">
              <li v-for="session in sessions" :key="session.id" class="a-session">
                <span class="a-session-day">{{ session.day || "—" }}</span>
                <span class="a-session-text">
                  <span class="a-session-title">{{ session.title }}</span>
                  <span v-if="session.attendees" class="a-muted">{{ session.attendees }}</span>
                  <span v-if="failedId === session.id && state.error" class="a-problem">
                    Couldn't add it: {{ state.error }}
                  </span>
                </span>
                <span class="a-session-action">
                  <template v-if="session.added">
                    <StatusWord status="measured" label="Added" />
                    <UiButton
                      variant="ghost"
                      small
                      icon-right="chevron-right"
                      :to="session.href"
                      :aria-label="`Open ${session.title}${session.day ? `, ${session.day}` : ''}`"
                    >
                      Open
                    </UiButton>
                  </template>
                  <span v-else-if="addingId === session.id" class="a-muted" role="status">Fetching from Livestorm…</span>
                  <UiButton
                    v-else
                    variant="primary"
                    small
                    icon="plus"
                    :disabled="Boolean(addingId)"
                    :aria-label="`${failedId === session.id ? 'Try again: add' : 'Add'} ${session.title}${session.day ? `, ${session.day}` : ''}`"
                    @click="add(session.id)"
                  >
                    {{ failedId === session.id ? "Try again" : "Add" }}
                  </UiButton>
                </span>
              </li>
            </ul>
            <div v-if="sessionsReady && state.eventSessionsNext" class="a-more">
              <UiButton variant="secondary" small :disabled="state.loading.eventSessionsMore" @click="loadMoreEventSessions().catch(() => {})">
                {{ state.loading.eventSessionsMore ? "Loading…" : `Show more (${sessions.length} of ${state.eventSessionsTotal ?? "?"})` }}
              </UiButton>
            </div>
          </div>
        </li>
      </ul>

      <div v-if="hasMore" class="a-more">
        <UiButton variant="ghost" icon="chevron-down" :disabled="state.loading.workspaceEvents" @click="loadMoreWorkspaceEvents">
          {{ state.loading.workspaceEvents ? "Loading more…" : "Load more events" }}
        </UiButton>
      </div>
    </Card>

    <Collapsible title="Have a link or an ID?" icon="copy" takeaway="Paste a session's or an event's Livestorm link or ID.">
      <div class="a-paste">
        <form class="a-paste-row" @submit.prevent="addPasted">
          <label class="a-label" for="a-session-input">Session link or ID</label>
          <div class="a-paste-controls">
            <input id="a-session-input" v-model="sessionInput" type="text" class="a-input" autocomplete="off" spellcheck="false" />
            <UiButton type="submit" variant="primary" icon="plus" :disabled="Boolean(addingId) || !sessionInput.trim()">
              {{ addingId && addingId === idFromInput(sessionInput).id ? "Fetching…" : "Add session" }}
            </UiButton>
          </div>
          <p v-if="sessionProblem" class="a-problem" role="alert">{{ sessionProblem }}</p>
        </form>
        <form class="a-paste-row" @submit.prevent="listPasted">
          <label class="a-label" for="a-event-input">Event link or ID</label>
          <div class="a-paste-controls">
            <input id="a-event-input" v-model="eventInput" type="text" class="a-input" autocomplete="off" spellcheck="false" />
            <UiButton type="submit" variant="secondary" icon="list" :disabled="!eventInput.trim()">List its sessions</UiButton>
          </div>
          <p v-if="eventProblem" class="a-problem" role="alert">{{ eventProblem }}</p>
        </form>
      </div>
    </Collapsible>

    <p class="a-note">
      Only finished sessions are listed. A session already added opens instead of being fetched again. A long recording's
      transcript can take a few minutes; its Report opens straight away.
    </p>
  </div>
</template>

<style scoped>
.a-page {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-6);
}

.a-crumbs {
  display: flex;
  align-items: center;
  gap: var(--siq-space-1);
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
}

.a-crumb {
  color: var(--siq-ink-2);
  text-decoration: none;
  border-radius: var(--siq-radius-inline);
}

.a-crumb:hover {
  color: var(--siq-accent);
}

.a-head {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-2);
}

.a-title {
  margin: 0;
  font-size: var(--siq-text-display);
  line-height: var(--siq-text-display-lh);
  font-weight: var(--siq-weight-regular);
  letter-spacing: -0.015em;
  color: var(--siq-ink);
}

.a-lead {
  margin: 0;
  max-width: 680px;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-ink-2);
}

.a-muted,
.a-note {
  margin: 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
}

.a-note {
  max-width: 680px;
}

.a-pad {
  padding: var(--siq-space-4) var(--siq-space-6);
}

/* ── The events card ── */
.a-events-head {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-4);
  padding: var(--siq-space-6);
  box-shadow: inset 0 -1px 0 var(--siq-hairline);
}

.a-filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--siq-space-3);
}

.a-field {
  position: relative;
  display: inline-flex;
  align-items: center;
}

.a-search {
  flex: 1 1 18rem;
}

/* v1 controls set their own font, colours, border and height: the classic
   element styles still load under v1. */
.a-input {
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

.a-input:hover {
  border-color: var(--siq-border-hover);
}

.a-input:focus-visible {
  outline: 2px solid var(--siq-focus);
  outline-offset: 1px;
}

.a-input::placeholder {
  color: var(--siq-muted);
}

.a-search .a-input {
  padding-left: 36px;
}

.a-field-icon,
.a-select-icon {
  position: absolute;
  color: var(--siq-muted);
  pointer-events: none;
}

.a-field-icon {
  left: var(--siq-space-3);
}

.a-select {
  appearance: none;
  padding-right: 36px;
  min-width: 12rem;
  cursor: pointer;
}

.a-select-icon {
  right: var(--siq-space-3);
}

.a-count {
  margin-left: auto;
}

/* ── Events and their sessions ── */
.a-list,
.a-session-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.a-item {
  box-shadow: inset 0 -1px 0 var(--siq-hairline-row);
}

.a-item:last-child {
  box-shadow: none;
}

.a-event {
  display: flex;
  align-items: center;
  gap: var(--siq-space-3);
  width: 100%;
  min-height: 64px;
  margin: 0;
  padding: var(--siq-space-3) var(--siq-space-6);
  border: 0;
  border-radius: 0;
  background: transparent;
  color: var(--siq-ink);
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition-property: background-color;
  transition-duration: var(--siq-duration);
  transition-timing-function: var(--siq-ease);
}

.a-event:hover {
  background: var(--siq-card-hover);
}

.a-event.siq-fx:focus-visible {
  outline: 2px solid var(--siq-focus);
  outline-offset: -3px;
}

.a-event-text,
.a-session-text {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1 1 auto;
}

.a-event-title,
.a-session-title {
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink);
  overflow-wrap: anywhere;
}

.a-chevron {
  flex: none;
  color: var(--siq-muted);
}

.a-sessions {
  padding: 0 var(--siq-space-6) var(--siq-space-4) calc(var(--siq-space-6) + var(--siq-space-4));
}

.a-count {
  margin: 0 0 var(--siq-space-2);
}

.a-more {
  padding-top: var(--siq-space-3);
}

.a-session {
  display: flex;
  align-items: center;
  gap: var(--siq-space-4);
  min-height: 56px;
  padding: var(--siq-space-2) 0;
  box-shadow: inset 0 1px 0 var(--siq-hairline-row);
}

.a-session-day {
  flex: none;
  width: 7.5rem;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-ink-2);
  font-variant-numeric: tabular-nums;
}

.a-session-action {
  display: inline-flex;
  align-items: center;
  gap: var(--siq-space-2);
  flex: none;
}

.a-more {
  padding: var(--siq-space-3) var(--siq-space-6);
  box-shadow: inset 0 1px 0 var(--siq-hairline);
}

/* ── Pasted IDs ── */
.a-paste {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-4);
}

.a-paste-row {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-1);
}

.a-label {
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink-2);
}

.a-paste-controls {
  display: flex;
  gap: var(--siq-space-2);
}

.a-paste-controls .a-input {
  flex: 1 1 auto;
  min-width: 0;
}

.a-problem {
  margin: 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-ink);
}

@media (max-width: 720px) {
  .a-title {
    font-size: var(--siq-text-headline);
    line-height: var(--siq-text-headline-lh);
  }
  .a-events-head {
    padding: var(--siq-space-4);
  }
  .a-field {
    flex: 1 1 100%;
  }
  .a-select {
    min-width: 0;
  }
  .a-count {
    margin-left: 0;
  }
  .a-event {
    padding: var(--siq-space-3) var(--siq-space-4);
  }
  .a-sessions {
    padding: 0 var(--siq-space-4) var(--siq-space-3);
  }
  .a-session {
    flex-wrap: wrap;
    gap: var(--siq-space-1) var(--siq-space-3);
  }
  .a-session-day {
    width: auto;
  }
  .a-session-text {
    flex-basis: 100%;
    order: -1;
  }
  .a-session-action {
    margin-left: auto;
  }
  .a-paste-controls {
    flex-direction: column;
  }
}

@media (forced-colors: active) {
  .a-item,
  .a-session,
  .a-events-head,
  .a-more {
    border-bottom: 1px solid CanvasText;
  }
}

@media (prefers-reduced-motion: reduce) {
  .a-event,
  .a-input {
    transition: none;
  }
}
</style>
