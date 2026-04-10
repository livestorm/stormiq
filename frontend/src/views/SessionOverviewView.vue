<script setup>
import { computed, ref } from "vue";
import DataTable from "../components/DataTable.vue";
import BarChartCard from "../components/charts/shared/BarChartCard.vue";
import ColumnChartCard from "../components/charts/shared/ColumnChartCard.vue";
import PieChartCard from "../components/charts/shared/PieChartCard.vue";
import { useWorkspace } from "../store/workspace";

const { state } = useWorkspace();
const activeTab = ref("summary");

function downloadBlob(filename, content, mimeType) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function downloadCsv(filename, rows) {
  if (!rows?.length) return;
  const columns = Object.keys(rows[0]);
  const escapeCell = (value) => {
    const text = String(value ?? "");
    if (/[",\n]/.test(text)) {
      return `"${text.replace(/"/g, '""')}"`;
    }
    return text;
  };
  const csv = [
    columns.join(","),
    ...rows.map((row) => columns.map((column) => escapeCell(row?.[column])).join(",")),
  ].join("\n");
  downloadBlob(filename, csv, "text/csv;charset=utf-8;");
}

const stats = computed(() => state.workspace?.stats?.sessionOverview || {});
const overviewRows = computed(() => state.workspace?.tables?.overview || []);
const peopleRows = computed(() => state.workspace?.tables?.people || []);
const countryRows = computed(() => state.workspace?.tables?.country || []);
const roleLabels = {
  team_member: "Team Members",
  participant: "Participants",
  guest_speaker: "Guest Speakers",
  owner: "Owner",
  viewer: "Viewers",
  moderator: "Moderators",
};
const roleRows = computed(() =>
  (state.workspace?.tables?.role || []).map((row) => ({
    ...row,
    role: roleLabels[row.role] ?? row.role,
  }))
);
const attendanceRows = computed(() => state.workspace?.tables?.attendanceDistribution || []);
const engagementRows = computed(() => state.workspace?.tables?.engagementTop || []);

const topMetrics = computed(() => [
  { label: "Registrants", value: stats.value.registrants_count ?? "0" },
  { label: "Attendees", value: stats.value.attendees_count ?? "0" },
  {
    label: "Attendance Rate",
    value: stats.value.attendance_rate_pct != null ? `${stats.value.attendance_rate_pct}%` : "n/a",
  },
  { label: "Replay Viewers", value: stats.value.replay_viewers_count ?? "0" },
  { label: "Chat Messages", value: stats.value.total_messages_count ?? "0" },
  { label: "Questions", value: stats.value.total_questions_count ?? "0" },
]);

const peopleTableRows = computed(() =>
  peopleRows.value.map((row) => ({
    full_name: row.full_name,
    email: row.email,
    ip_country_name: row.ip_country_name,
    ip_city: row.ip_city,
    attendance_rate: row.attendance_rate,
  }))
);

const peopleColumnLabels = {
  full_name: "Name",
  email: "Email",
  ip_country_name: "Country",
  ip_city: "City",
  attendance_rate: "Attendance Rate",
};

const peopleColumnWidths = {
  full_name: "18rem",
  email: "18rem",
  ip_country_name: "10rem",
  ip_city: "10rem",
  attendance_rate: "10rem",
};

const engagementTableRows = computed(() =>
  engagementRows.value.map((row) => ({
    full_name: row.full_name,
    company: row.company,
    job_title: row.job_title,
    attendance_duration: row.attendance_duration_label,
    messages_count: row.messages_count,
    questions_count: row.questions_count,
    up_votes_count: row.up_votes_count,
    engagement_score: row.engagement_score,
  }))
);

const tabs = [
  { id: "summary", label: "Summary" },
  { id: "people", label: "People" },
  { id: "charts", label: "Charts" },
];

const hasOverviewData = computed(
  () =>
    overviewRows.value.length > 0 ||
    peopleRows.value.length > 0 ||
    countryRows.value.length > 0 ||
    roleRows.value.length > 0 ||
    attendanceRows.value.length > 0 ||
    engagementRows.value.length > 0 ||
    Object.keys(stats.value || {}).length > 0,
);

const isOverviewLoading = computed(
  () =>
    state.loading.sessionFetch &&
    (
      (state.inputMode === "session" && Boolean(state.sessionId.trim())) ||
      (state.inputMode === "event" && Boolean(state.selectedEventSessionId.trim()))
    )
);
</script>

<template>
  <section class="page-section">
    <h2>Session Overview</h2>
    <p class="page-description">High-level session context, attendee signals, and engagement snapshots.</p>

    <section v-if="isOverviewLoading" class="panel loading-panel">
      <div class="loading-indicator" aria-hidden="true"></div>
      <div>
        <h3 class="loading-title">Session Overview is loading</h3>
        <p class="loading-copy">We’re loading the session context first so you can start reviewing the event as quickly as possible.</p>
      </div>
    </section>

    <template v-else-if="state.workspace && hasOverviewData">
      <div class="metric-grid">
        <article class="metric-card metric-card-hero" v-for="metric in topMetrics" :key="metric.label">
          <span class="metric-label">{{ metric.label }}</span>
          <strong class="metric-value">{{ metric.value }}</strong>
        </article>
      </div>

      <div class="section-tabs">
        <button
          v-for="tab in tabs"
          :key="tab.id"
          type="button"
          class="section-tab"
          :class="{ active: activeTab === tab.id }"
          @click="activeTab = tab.id"
        >
          {{ tab.label }}
        </button>
      </div>

      <template v-if="activeTab === 'summary'">
        <section class="panel">
          <div class="panel-heading panel-heading-inline">
            <div class="panel-heading-inline-title">
              <h3>Session payload</h3>
              <button class="inline-icon-button" type="button" title="Download session payload CSV" @click="downloadCsv('session-payload.csv', overviewRows)">
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path
                    d="M12 3v10.2l3.6-3.6 1.4 1.4-6 6-6-6 1.4-1.4 3.6 3.6V3H12zm-7 14h14v2H5v-2z"
                    fill="currentColor"
                  />
                </svg>
              </button>
            </div>
          </div>
          <DataTable :rows="overviewRows" csv-filename="session-payload.csv" :show-toolbar="false" />
        </section>
      </template>

      <template v-else-if="activeTab === 'people'">
        <section class="panel">
          <div class="panel-heading panel-heading-inline">
            <div class="panel-heading-inline-title">
              <h3>List of people</h3>
              <button class="inline-icon-button" type="button" title="Download people CSV" @click="downloadCsv('session-people.csv', peopleTableRows)">
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path
                    d="M12 3v10.2l3.6-3.6 1.4 1.4-6 6-6-6 1.4-1.4 3.6 3.6V3H12zm-7 14h14v2H5v-2z"
                    fill="currentColor"
                  />
                </svg>
              </button>
            </div>
          </div>
          <DataTable :rows="peopleTableRows" :column-labels="peopleColumnLabels" :column-widths="peopleColumnWidths" csv-filename="session-people.csv" :show-toolbar="false" />
        </section>

        <section class="panel" v-if="engagementTableRows.length">
          <div class="panel-heading panel-heading-inline">
            <div class="panel-heading-inline-title">
              <h3>Most Engaged People</h3>
              <button class="inline-icon-button" type="button" title="Download most engaged people CSV" @click="downloadCsv('most-engaged-people.csv', engagementTableRows)">
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path
                    d="M12 3v10.2l3.6-3.6 1.4 1.4-6 6-6-6 1.4-1.4 3.6 3.6V3H12zm-7 14h14v2H5v-2z"
                    fill="currentColor"
                  />
                </svg>
              </button>
            </div>
          </div>
          <DataTable :rows="engagementTableRows" csv-filename="most-engaged-people.csv" :show-toolbar="false" />
        </section>
      </template>

      <template v-else>
        <div class="overview-chart-grid">
          <BarChartCard
            title="Attendance By Country"
            description="Top countries represented in the room."
            :rows="countryRows"
            label-key="ip_country_name"
            value-key="people_count"
          />

          <PieChartCard
            title="People By Role"
            description="Participant role mix for this session."
            :rows="roleRows"
            label-key="role"
            value-key="people_count"
          />
        </div>

        <ColumnChartCard
          title="Attendance Rate Distribution"
          description="How much of the session attendees actually watched."
          :rows="attendanceRows"
          label-key="attendance_band"
          value-key="people_count"
        />
      </template>
    </template>
  </section>
</template>
