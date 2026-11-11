<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { RouterLink } from "vue-router";
import SessionSparkline from "../components/report/SessionSparkline.vue";
import ColumnFilter from "../components/sessions/ColumnFilter.vue";
import EventPicker from "../components/sessions/EventPicker.vue";
import FloatingPanel from "../components/sessions/FloatingPanel.vue";
import SearchToggle from "../components/sessions/SearchToggle.vue";
import Card from "../components/ui/Card.vue";
import Icon from "../components/ui/Icon.vue";
import StatCard from "../components/ui/StatCard.vue";
import StatusWord from "../components/ui/StatusWord.vue";
import UiButton from "../components/ui/UiButton.vue";
import { safeStorage } from "../design/theme";
import { libraryCells } from "../sessions/library";
import {
  COLUMNS,
  DEFAULT_SORT,
  activeFilters,
  ariaSort,
  clearFilter,
  emptyFilters,
  eventOptions,
  isFiltered,
  nextSort,
  parseSort,
  sessionCounts,
  sessionRow,
  shownLabel,
  teammateOptions,
  visibleSessions,
} from "../sessions/list";
import { createLibraryRows } from "../sessions/useLibraryRows";
import { useWorkspace } from "../store/workspace";

// The v1 Sessions page: every session the organization has fetched, as one
// index table. The list comes from the store (`loadWorkspaceSessions`,
// `GET /api/workspace-sessions`); `sessions/list.js` filters, sorts and
// words it. A column's title sorts by it (a second click reverses), its
// funnel opens that column's filter, and the filters that are on show as
// chips in the toolbar. On a phone, where the table becomes blocks, the
// same sorts and filters live in a "Sort & filter" sheet. A row opens the
// session's Report. Each row's shape, audience numbers and lead finding
// come from the session's own record (`GET /record/library`), asked only
// for the rows that come on screen (`sessions/useLibraryRows.js`).

const { state, loadWorkspaceSessions } = useWorkspace();

const STALE_AFTER_MS = 60 * 1000;
function load() {
  if (!state.auth.connectedUser && !state.auth.allowLocalApiKeyFallback) return;
  const stale = Date.now() - (state.workspaceSessionsLoadedAt || 0) > STALE_AFTER_MS;
  if (!state.workspaceSessions.length || stale) loadWorkspaceSessions().catch(() => {});
}
onMounted(load);
watch(() => [state.auth.connectedUser, state.auth.allowLocalApiKeyFallback], load);

// ── Sort (remembered in this browser) and filters (for this visit) ──
const SORT_KEY = "stormiq:sessions-sort";
const storage = safeStorage();
function readSort() {
  try {
    return parseSort(storage?.getItem(SORT_KEY));
  } catch {
    return { ...DEFAULT_SORT };
  }
}
const sort = ref(readSort());
watch(sort, (value) => {
  try {
    storage?.setItem(SORT_KEY, JSON.stringify(value));
  } catch {
    // Storage full or blocked: the sort lasts for this visit.
  }
});
function sortBy(key) {
  sort.value = nextSort(sort.value, key);
}

const filters = ref(emptyFilters());
function setFilter(key, value) {
  filters.value = { ...filters.value, [key]: value };
}
const query = computed({ get: () => filters.value.query, set: (value) => setFilter("query", value) });
const event = computed({ get: () => filters.value.event, set: (value) => setFilter("event", value) });
const chips = computed(() => activeFilters(filters.value));
const filtered = computed(() => isFiltered(filters.value));
function removeChip(key) {
  filters.value = clearFilter(filters.value, key);
}
function clearAll() {
  filters.value = emptyFilters();
}

// The table's columns: which sort (`sort`) and which filter (`filter`).
// The library's cells load row by row, so they neither sort nor filter.
const HEAD = [
  { key: "date", label: "Date", sort: "date", filter: "Session date", name: "date" },
  { key: "session", label: "Session", sort: "session", filter: "Length", name: "length" },
  { key: "shape", label: "Audience chat", srBefore: "Shape: " },
  { key: "attendees", label: "Attendees", sort: "attendees", filter: "Attendees", name: "attendees", cls: "s-num" },
  { key: "wrote", label: "Wrote", cls: "s-figure" },
  { key: "questions", label: "Questions", cls: "s-figure" },
  { key: "transcript", label: "Transcript", sort: "transcript", filter: "Transcript", name: "transcript", align: "end" },
  { key: "added", label: "Added", sort: "added", filter: "Added by", name: "who added it", align: "end" },
];
const FILTERABLE = HEAD.filter((column) => column.filter);
const SORT_OPTIONS = HEAD.filter((column) => column.sort).flatMap((column) =>
  [COLUMNS[column.sort].first, COLUMNS[column.sort].first === "asc" ? "desc" : "asc"].map((dir) => ({
    value: `${column.sort}:${dir}`,
    label: `${column.label}, ${COLUMNS[column.sort][dir].toLowerCase()}`,
  })),
);
const sortValue = computed({
  get: () => `${sort.value.key}:${sort.value.dir}`,
  set: (value) => {
    const [key, dir] = String(value).split(":");
    sort.value = parseSort({ key, dir });
  },
});

function sortIcon(key) {
  if (sort.value.key !== key) return "arrow-up-down";
  return sort.value.dir === "asc" ? "arrow-up" : "arrow-down";
}
function filterChip(columnKey) {
  return chips.value.find((chip) => chip.column === columnKey) || null;
}
function filterLabel(column) {
  const chip = filterChip(column.key);
  return chip ? `Filter by ${column.name}: ${chip.label}` : `Filter by ${column.name}`;
}

// One panel open at a time: a column's key, "sheet" (the phone's "Sort &
// filter") or "".
const panel = ref("");
const filterButtons = {};
const sheetButton = ref(null);
const panelColumn = computed(() => FILTERABLE.find((column) => column.key === panel.value) || null);
function togglePanel(key) {
  panel.value = panel.value === key ? "" : key;
}
function closePanel() {
  panel.value = "";
}

const sessions = computed(() => state.workspaceSessions || []);
const loading = computed(() => state.loading.workspaceSessions && !sessions.value.length);
const counts = computed(() => sessionCounts(sessions.value));
const events = computed(() => eventOptions(sessions.value));
const teammates = computed(() => teammateOptions(sessions.value));
const rows = computed(() =>
  visibleSessions(sessions.value, { filters: filters.value, sort: sort.value }).map((session) => sessionRow(session)),
);
const shown = computed(() => shownLabel(rows.value.length, counts.value.sessions, filtered.value));

// ── The library's cells, for the rows on screen ──
const library = createLibraryRows();
const cells = computed(() => {
  const out = {};
  for (const [id, entry] of Object.entries(library.state.rows)) out[id] = { loading: entry.loading, ...(libraryCells(entry.body, id) || {}) };
  return out;
});
// Made at setup, before the rows' refs run: a row is asked for once it comes
// within 200px of the screen. Without IntersectionObserver, every row is.
const observer =
  typeof IntersectionObserver === "undefined"
    ? null
    : new IntersectionObserver(
        (entries) => entries.forEach((entry) => entry.isIntersecting && library.want(entry.target.dataset.sessionId)),
        { rootMargin: "200px 0px" },
      );
function watchRow(element) {
  if (!element) return;
  if (observer) observer.observe(element);
  else library.want(element.dataset.sessionId);
}
onBeforeUnmount(() => {
  observer?.disconnect();
  library.close();
});
</script>

<template>
  <div class="s-page">
    <header class="s-head">
      <h1 class="s-title">Sessions</h1>
      <UiButton variant="primary" icon="plus" to="/sessions/add">Add session</UiButton>
    </header>

    <p v-if="loading" class="s-status" role="status">Loading your sessions…</p>

    <Card v-else-if="!sessions.length" class="s-empty" padding="hero">
      <h2 class="s-empty-title">No sessions yet</h2>
      <p class="s-empty-text">Add a finished Livestorm session to see its report here. Your teammates' sessions show up too.</p>
      <UiButton variant="secondary" icon="plus" to="/sessions/add">Add your first session</UiButton>
    </Card>

    <template v-else>
      <section class="s-counts" aria-label="Your organization's sessions">
        <StatCard icon="calendar" label="Sessions" :value="String(counts.sessions)" />
        <StatCard icon="file-text" label="With a transcript" :value="String(counts.transcripts)" />
        <StatCard icon="list" label="Events" :value="String(counts.events)" />
      </section>

      <Card padding="flush" class="s-index">
        <div class="s-toolbar" role="search" aria-label="Sessions">
          <EventPicker v-if="events.length > 1" v-model="event" :events="events" />
          <ul v-if="chips.length" class="s-chips" aria-label="Filters on">
            <li v-for="chip in chips" :key="chip.key">
              <button type="button" class="s-chip siq-fx" :aria-label="`Remove filter: ${chip.label}`" @click="removeChip(chip.key)">
                <span class="s-chip-text">{{ chip.label }}</span>
                <Icon name="x" :size="14" />
              </button>
            </li>
          </ul>
          <button v-if="filtered" type="button" class="s-clear siq-fx" @click="clearAll">Clear all</button>
          <p class="s-shown" role="status">{{ shown }}</p>
          <SearchToggle v-model="query" class="s-search" />
          <button
            ref="sheetButton"
            type="button"
            class="s-sheet-button siq-fx"
            :class="{ 's-sheet-on': chips.length }"
            aria-haspopup="dialog"
            :aria-expanded="panel === 'sheet' ? 'true' : 'false'"
            @click="togglePanel('sheet')"
          >
            <Icon name="list-filter" :size="16" />
            <span>Sort &amp; filter</span>
            <span v-if="chips.length" class="s-sheet-count">{{ chips.length }}</span>
          </button>
        </div>

        <div v-if="rows.length" class="s-scroll">
          <table class="s-table">
            <caption class="siq-sr-only">Your organization's sessions</caption>
            <thead>
              <tr>
                <th
                  v-for="column in HEAD"
                  :key="column.key"
                  scope="col"
                  :class="column.cls"
                  :aria-sort="column.sort ? ariaSort(sort, column.sort) : undefined"
                >
                  <span class="s-th">
                    <button
                      v-if="column.sort"
                      type="button"
                      class="s-sort siq-fx"
                      :class="{ 's-sort-on': sort.key === column.sort }"
                      @click="sortBy(column.sort)"
                    >
                      <span>{{ column.label }}</span>
                      <Icon :name="sortIcon(column.sort)" :size="14" class="s-sort-icon" />
                    </button>
                    <span v-else class="s-th-label"><span v-if="column.srBefore" class="siq-sr-only">{{ column.srBefore }}</span>{{ column.label }}</span>
                    <button
                      v-if="column.filter"
                      :ref="(element) => (filterButtons[column.key] = element)"
                      type="button"
                      class="s-filter siq-fx"
                      :class="{ 's-filter-on': filterChip(column.key) }"
                      :aria-label="filterLabel(column)"
                      aria-haspopup="dialog"
                      :aria-expanded="panel === column.key ? 'true' : 'false'"
                      @click="togglePanel(column.key)"
                    >
                      <Icon name="list-filter" :size="16" />
                      <span v-if="filterChip(column.key)" class="s-filter-dot" aria-hidden="true" />
                    </button>
                  </span>
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in rows" :key="row.id" :ref="watchRow" class="s-row" :data-session-id="row.id">
                <td class="s-date">{{ row.day }}</td>
                <td class="s-session">
                  <!-- The title's link covers the whole row (::after). -->
                  <RouterLink :to="row.href" class="s-link siq-fx">{{ row.title }}</RouterLink>
                  <span class="s-meta">
                    <span v-if="row.subtitle">{{ row.subtitle }}</span>
                    <span v-if="row.subtitle && row.length" aria-hidden="true"> · </span>
                    <span v-if="row.length">{{ row.length }}</span>
                  </span>
                  <span v-if="cells[row.id]?.lead" class="s-lead">{{ cells[row.id].lead }}</span>
                </td>
                <td class="s-spark">
                  <SessionSparkline
                    v-if="cells[row.id] && !cells[row.id].loading"
                    :points="cells[row.id].sparkline"
                    :label="cells[row.id].sparkLabel"
                  />
                </td>
                <td class="s-num">
                  <span class="s-attendees">{{ row.attendees }}</span>
                  <span v-if="row.registrants" class="s-meta">{{ row.registrants }}</span>
                </td>
                <td class="s-figure s-wrote">
                  <template v-if="cells[row.id] && !cells[row.id].loading">
                    {{ cells[row.id].wrote }}<span v-if="cells[row.id].wroteEstimated" class="s-meta" title="Estimated, as on the Report"> est.</span>
                  </template>
                </td>
                <td class="s-figure s-questions">
                  <template v-if="cells[row.id] && !cells[row.id].loading">
                    {{ cells[row.id].questions }}<span v-if="cells[row.id].open" class="s-meta"> · {{ cells[row.id].open }}</span>
                  </template>
                </td>
                <td class="s-transcript">
                  <StatusWord v-if="row.transcript === 'ready'" status="measured" label="Ready" />
                  <span v-else class="s-meta">Not yet</span>
                </td>
                <td class="s-added">
                  <span class="s-added-name">{{ row.addedBy || "—" }}</span>
                  <span v-if="row.addedDay" class="s-meta">{{ row.addedDay }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="s-none">
          <p class="s-empty-text">No session matches.</p>
          <UiButton variant="ghost" icon="x" @click="clearAll">Clear all filters</UiButton>
        </div>
      </Card>
      <p class="s-note">
        Attendees and registrants are Livestorm's counts, your team included. Audience chat, wrote and questions are the
        audience's alone, as on each session's Report ("est." marks an estimate), and the line under a title is what
        stood out most.
      </p>

      <!-- A column's filter, under its funnel. -->
      <FloatingPanel
        :open="Boolean(panelColumn)"
        :anchor="panelColumn ? filterButtons[panelColumn.key] : null"
        :label="panelColumn ? `Filter by ${panelColumn.name}` : 'Filter'"
        :align="panelColumn?.align || 'start'"
        @close="closePanel"
      >
        <ColumnFilter
          v-if="panelColumn"
          :column="panelColumn.key"
          :filters="filters"
          :teammates="teammates"
          @set="setFilter"
          @done="closePanel"
        />
      </FloatingPanel>

      <!-- The phone's "Sort & filter" sheet: the same sorts and filters. -->
      <FloatingPanel :open="panel === 'sheet'" :anchor="sheetButton" label="Sort and filter" align="end" sheet @close="closePanel">
        <div class="s-sheet">
          <div class="s-sheet-head">
            <h2 class="s-sheet-title">Sort &amp; filter</h2>
            <UiButton variant="icon" icon="x" label="Close" @click="closePanel" />
          </div>
          <label class="s-sheet-label" for="s-sheet-sort">Sort by</label>
          <span class="s-select-wrap">
            <select id="s-sheet-sort" v-model="sortValue" class="s-select">
              <option v-for="option in SORT_OPTIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
            </select>
            <Icon name="chevron-down" :size="16" class="s-select-icon" />
          </span>
          <section v-for="column in FILTERABLE" :key="column.key" class="s-sheet-part" :aria-label="column.filter">
            <h3 class="s-sheet-label">{{ column.filter }}</h3>
            <ColumnFilter :column="column.key" :filters="filters" :teammates="teammates" :autofocus="false" @set="setFilter" />
          </section>
          <div class="s-sheet-foot">
            <UiButton variant="ghost" :disabled="!filtered" @click="clearAll">Clear all</UiButton>
            <UiButton variant="primary" @click="closePanel">Show {{ shown }}</UiButton>
          </div>
        </div>
      </FloatingPanel>
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

/* ── The toolbar: event, filter chips, count, search ── */
.s-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--siq-space-2);
  padding: var(--siq-space-3) var(--siq-space-6);
  box-shadow: inset 0 -1px 0 var(--siq-hairline);
}

.s-chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--siq-space-2);
  min-width: 0;
  margin: 0;
  padding: 0;
  list-style: none;
}

.s-chips li {
  display: flex;
  min-width: 0;
  max-width: 100%;
}

.s-chip {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: var(--siq-space-1);
  max-width: 100%;
  min-height: var(--siq-target);
  margin: 0;
  padding: 0 var(--siq-space-2) 0 var(--siq-space-3);
  border: 0;
  border-radius: var(--siq-radius-pill);
  background: var(--siq-selected-bg);
  color: var(--siq-selected-ink);
  font: inherit;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-weight: var(--siq-weight-medium);
  cursor: pointer;
  transition-property: background-color, box-shadow;
  transition-duration: var(--siq-duration);
  transition-timing-function: var(--siq-ease);
}

.s-chip:hover {
  box-shadow: inset 0 0 0 1px var(--siq-accent);
}

.s-chip-text {
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.s-clear {
  min-height: var(--siq-target);
  margin: 0;
  padding: 0 var(--siq-space-2);
  border: 0;
  border-radius: var(--siq-radius-control);
  background: transparent;
  color: var(--siq-accent);
  font: inherit;
  font-size: var(--siq-text-small);
  font-weight: var(--siq-weight-medium);
  cursor: pointer;
}

.s-clear:hover {
  color: var(--siq-accent-hover);
  text-decoration: underline;
}

.s-shown {
  margin-left: auto;
  white-space: nowrap;
}

/* Only on a phone, where the table's titles are hidden. */
.s-sheet-button {
  position: relative;
  display: none;
  align-items: center;
  gap: var(--siq-space-2);
  height: var(--siq-target);
  margin: 0;
  padding: 0 var(--siq-space-3);
  border: 1px solid var(--siq-border);
  border-radius: var(--siq-radius-control);
  background: var(--siq-input);
  color: var(--siq-ink);
  font: inherit;
  font-size: var(--siq-text-ui);
  cursor: pointer;
}

.s-sheet-button.s-sheet-on {
  border-color: var(--siq-accent);
}

.s-sheet-count {
  min-width: 20px;
  padding: 0 6px;
  border-radius: var(--siq-radius-pill);
  background: var(--siq-selected-bg);
  color: var(--siq-selected-ink);
  font-size: var(--siq-text-caption);
  line-height: 20px;
  font-weight: var(--siq-weight-medium);
  text-align: center;
  font-variant-numeric: tabular-nums;
}

/* ── The index ── */
/* Scrolls sideways on a narrow desktop window rather than widening the page. */
.s-scroll {
  position: relative;
  overflow-x: auto;
}

.s-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-ink);
}

.s-table th,
.s-table td {
  padding: var(--siq-space-3);
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
  padding-top: var(--siq-space-1);
  padding-bottom: var(--siq-space-1);
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-muted);
  white-space: nowrap;
  box-shadow: inset 0 -1px 0 var(--siq-hairline-head);
}

/* A column's title (its sort) and its funnel (its filter). */
.s-th {
  display: inline-flex;
  align-items: center;
  min-height: var(--siq-target);
}

.s-table th.s-num .s-th {
  justify-content: flex-end;
}

.s-sort,
.s-filter {
  position: relative;
  display: inline-flex;
  align-items: center;
  height: var(--siq-target);
  margin: 0;
  border: 0;
  border-radius: var(--siq-radius-inline);
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
  transition-property: color, background-color;
  transition-duration: var(--siq-duration);
  transition-timing-function: var(--siq-ease);
}

.s-sort {
  gap: var(--siq-space-1);
  margin-left: calc(-1 * var(--siq-space-2));
  padding: 0 var(--siq-space-2);
}

.s-sort:hover,
.s-sort-on {
  color: var(--siq-ink);
}

.s-sort-icon {
  opacity: 0;
  transition-property: opacity;
  transition-duration: var(--siq-duration);
  transition-timing-function: var(--siq-ease);
}

.s-sort:hover .s-sort-icon,
.s-sort:focus-visible .s-sort-icon {
  opacity: 0.6;
}

.s-sort-on .s-sort-icon,
.s-sort-on:hover .s-sort-icon {
  opacity: 1;
  color: var(--siq-accent);
}

/* 32px drawn, 40px to hit: the ::before reaches into the cell's padding. */
.s-filter {
  justify-content: center;
  width: 32px;
  padding: 0;
  color: var(--siq-muted);
}

.s-filter::before {
  content: "";
  position: absolute;
  inset: 0 -4px;
}

.s-filter:hover,
.s-filter[aria-expanded="true"] {
  color: var(--siq-ink);
  background: var(--siq-card-hover);
}

.s-filter-on {
  color: var(--siq-accent);
}

.s-filter-dot {
  position: absolute;
  top: 9px;
  right: 9px;
  width: 7px;
  height: 7px;
  border-radius: var(--siq-radius-pill);
  background: var(--siq-accent);
  box-shadow: 0 0 0 2px var(--siq-card);
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

/* "53 of 57" on one line. */
.s-table td.s-num {
  white-space: nowrap;
}

.s-num .s-meta {
  margin-left: var(--siq-space-1);
}

.s-added {
  color: var(--siq-ink-2);
}

.s-added-name,
.s-added .s-meta {
  display: block;
}

.s-added .s-meta {
  white-space: nowrap;
}

.s-lead {
  display: block;
  margin-top: var(--siq-space-1);
  max-width: 48ch;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-ink-2);
}

.s-spark {
  width: 96px;
}

.s-table .s-figure {
  text-align: right;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.s-none {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--siq-space-2);
  padding: var(--siq-space-6);
}

/* ── The phone's sheet ── */
.s-sheet {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-2);
}

.s-sheet-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--siq-space-2);
}

.s-sheet-title {
  margin: 0;
  font-size: var(--siq-text-card-title);
  line-height: var(--siq-text-card-title-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink);
}

.s-sheet-part {
  padding-top: var(--siq-space-2);
  box-shadow: inset 0 1px 0 var(--siq-hairline);
}

.s-sheet-label {
  margin: 0;
  padding: var(--siq-space-1) 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-muted);
}

.s-select-wrap {
  position: relative;
  display: flex;
  align-items: center;
}

.s-select {
  width: 100%;
  height: var(--siq-target);
  margin: 0;
  padding: 0 36px 0 var(--siq-space-3);
  border: 1px solid var(--siq-border);
  border-radius: var(--siq-radius-control);
  background: var(--siq-input);
  color: var(--siq-ink);
  font: inherit;
  appearance: none;
  cursor: pointer;
}

.s-select:focus-visible {
  outline: 2px solid var(--siq-focus);
  outline-offset: 1px;
}

.s-select-icon {
  position: absolute;
  right: var(--siq-space-3);
  color: var(--siq-muted);
  pointer-events: none;
}

.s-sheet-foot {
  position: sticky;
  bottom: calc(-1 * var(--siq-space-4));
  display: flex;
  justify-content: space-between;
  gap: var(--siq-space-2);
  margin: var(--siq-space-2) calc(-1 * var(--siq-space-4)) calc(-1 * var(--siq-space-4));
  padding: var(--siq-space-3) var(--siq-space-4);
  background: var(--siq-card);
  box-shadow: inset 0 1px 0 var(--siq-hairline);
}

/* On a phone each row becomes a small block: the title first, then the
   date, attendees and transcript on one line. The columns' titles are
   hidden, so their sorts and filters move to the "Sort & filter" sheet. */
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
  .s-toolbar {
    padding: var(--siq-space-3) var(--siq-space-4);
  }
  /* The event on its own row, then Sort & filter, the search (a row of
     its own while open) and the chips, then the count. */
  .s-toolbar :deep(.ep-button) {
    flex: 1 1 100%;
    max-width: none;
  }
  .s-toolbar :deep(.ep-button .ep-label) {
    flex: 1 1 auto;
    text-align: left;
  }
  .s-sheet-button {
    display: inline-flex;
    order: 1;
  }
  .s-search {
    order: 2;
  }
  .s-shown {
    order: 10;
    flex: 1 1 100%;
    margin-left: 0;
  }
  .s-search.st-open {
    order: 4;
    flex: 1 1 100%;
  }
  .s-chips {
    order: 5;
  }
  .s-clear {
    order: 6;
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
      "session session spark"
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
  .s-spark {
    grid-area: spark;
    justify-self: end;
    width: auto;
  }
  .s-added,
  .s-table .s-figure {
    display: none;
  }
}

@media (forced-colors: active) {
  .s-table th,
  .s-table td,
  .s-toolbar,
  .s-sheet-part {
    border-bottom: 1px solid CanvasText;
  }
  .s-chip,
  .s-filter-on {
    border: 1px solid Highlight;
  }
  .s-filter-dot {
    background: Highlight;
  }
}

@media (prefers-reduced-motion: reduce) {
  .s-row,
  .s-chip,
  .s-sort,
  .s-sort-icon,
  .s-filter {
    transition: none;
  }
}
</style>
