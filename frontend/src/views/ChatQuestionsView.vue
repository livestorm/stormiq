<script setup>
import { computed, ref } from "vue";
import ActivityTimelineChartCard from "../components/charts/chat-questions/ActivityTimelineChartCard.vue";
import BarChartCard from "../components/charts/shared/BarChartCard.vue";
import ContributorsComparisonChartCard from "../components/charts/chat-questions/ContributorsComparisonChartCard.vue";
import DataTable from "../components/DataTable.vue";
import { useWorkspace } from "../store/workspace";

const { state } = useWorkspace();
const activeTab = ref("chat");

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

function parseUtcDate(value) {
  const text = String(value || "").trim();
  if (!text) return null;
  const normalized = text.endsWith("UTC") ? text.replace(" UTC", "Z") : text;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatUtcMinuteLabel(date) {
  return new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
    hour12: false,
  }).format(date);
}

const chatRows = computed(() => state.workspace?.tables?.chat || []);
const questionRows = computed(() => state.workspace?.tables?.questions || []);
const chatStats = computed(() => state.workspace?.stats?.chat || {});
const questionStats = computed(() => state.workspace?.stats?.questions || {});
const activityTimelineRowsRaw = computed(() => state.workspace?.tables?.chatQuestionsTimeline || []);
const reactionMomentsRowsRaw = computed(() => state.workspace?.tables?.chatQuestionReactionMoments || []);

const avgMessageLength = computed(() => {
  const statValue = Number(chatStats.value?.text_length?.avg_chars || 0);
  if (statValue > 0) return statValue;
  const lengths = chatRows.value
    .map((row) => String(row?.text_content || ""))
    .filter((text) => text.length > 0)
    .map((text) => text.length);
  if (!lengths.length) return 0;
  const total = lengths.reduce((sum, length) => sum + length, 0);
  return total / lengths.length;
});

const uniqueChattersCount = computed(() => {
  const statValue = Number(chatStats.value?.unique_authors || 0);
  if (statValue > 0) return statValue;
  return new Set(
    chatRows.value
      .map((row) => String(row?.author_id || "").trim())
      .filter(Boolean),
  ).size;
});

const uniqueAskersCount = computed(() => {
  const statValue = Number(questionStats.value?.unique_askers || 0);
  if (statValue > 0) return statValue;
  return new Set(
    questionRows.value
      .map((row) => String(row?.asked_by || row?.question_author_id || "").trim())
      .filter(Boolean),
  ).size;
});

const topMetrics = computed(() => [
  { label: "Messages", value: String(Number(chatStats.value?.total_messages || chatRows.value.length || 0)) },
  { label: "Unique Chatters", value: String(uniqueChattersCount.value) },
  { label: "Avg Msg Length", value: `${avgMessageLength.value.toFixed(1)} chars` },
  { label: "Questions", value: String(Number(questionStats.value?.total_questions || questionRows.value.length || 0)) },
  { label: "Unique Askers", value: String(uniqueAskersCount.value) },
]);

const chatTableRows = computed(() =>
  chatRows.value.map((row) => ({
    text_content: row.text_content,
    created_at: row.created_at,
    updated_at: row.updated_at,
    author_id: row.author_id,
  }))
);

const chatColumnLabels = {
  text_content: "Message",
  created_at: "Created At",
  updated_at: "Updated At",
  author_id: "Author ID",
};

const chatColumnWidths = {
  text_content: "24rem",
  created_at: "14rem",
  updated_at: "14rem",
  author_id: "16rem",
};

const questionsTableRows = computed(() =>
  questionRows.value.map((row) => ({
    question: row.question,
    asked_by: row.asked_by || row.question_author_id,
    asked_at: row.asked_at || row.created_at,
    responded_by: row.answered_by || row.responded_by || row.response_author_id,
    responded_at: row.responded_at,
    response: row.response,
  }))
);

const questionsColumnLabels = {
  question: "Question",
  asked_by: "Asked By",
  asked_at: "Asked At",
  responded_by: "Responded By",
  responded_at: "Responded At",
  response: "Response",
};

const questionsColumnWidths = {
  question: "28rem",
  asked_by: "14rem",
  asked_at: "14rem",
  responded_by: "14rem",
  responded_at: "14rem",
  response: "22rem",
};

const topChatContributorsRows = computed(() => {
  const counts = new Map();
  for (const row of chatTableRows.value) {
    const author = String(row.author_id || "").trim();
    if (!author) continue;
    counts.set(author, (counts.get(author) || 0) + 1);
  }
  return Array.from(counts.entries())
    .map(([author, messages]) => ({ contributor: author, messages }))
    .sort((a, b) => b.messages - a.messages)
    .slice(0, 10);
});

const topQuestionContributorsRows = computed(() => {
  const counts = new Map();
  for (const row of questionsTableRows.value) {
    const asker = String(row.asked_by || "").trim();
    if (!asker) continue;
    counts.set(asker, (counts.get(asker) || 0) + 1);
  }
  return Array.from(counts.entries())
    .map(([asker, questions]) => ({ contributor: asker, questions }))
    .sort((a, b) => b.questions - a.questions)
    .slice(0, 10);
});

const contributorsComparisonRows = computed(() => [
  ...topChatContributorsRows.value.map((row) => ({
    contributor: row.contributor,
    count: row.messages,
    kind: "Messages",
  })),
  ...topQuestionContributorsRows.value.map((row) => ({
    contributor: row.contributor,
    count: row.questions,
    kind: "Questions",
  })),
]);

const activityTimelineRows = computed(() => {
  const buckets = new Map();

  for (const row of chatTableRows.value) {
    const date = parseUtcDate(row.created_at);
    if (!date) continue;
    const minuteDate = new Date(date);
    minuteDate.setUTCSeconds(0, 0);
    const key = minuteDate.toISOString();
    const label = formatUtcMinuteLabel(minuteDate);
    const current = buckets.get(key) || { label, messages: 0, questions: 0, sortTime: minuteDate.getTime() };
    current.messages += 1;
    buckets.set(key, current);
  }

  for (const row of questionsTableRows.value) {
    const date = parseUtcDate(row.asked_at || row.created_at);
    if (!date) continue;
    const minuteDate = new Date(date);
    minuteDate.setUTCSeconds(0, 0);
    const key = minuteDate.toISOString();
    const label = formatUtcMinuteLabel(minuteDate);
    const current = buckets.get(key) || { label, messages: 0, questions: 0, sortTime: minuteDate.getTime() };
    current.questions += 1;
    buckets.set(key, current);
  }

  if (!buckets.size && activityTimelineRowsRaw.value.length) {
    return activityTimelineRowsRaw.value.map((row, index) => ({
      label: row.progress_window || `Window ${index + 1}`,
      messages: Number(row.chat_messages) || 0,
      questions: Number(row.question_count) || 0,
      sortTime: index,
    }));
  }

  return Array.from(buckets.values()).sort((a, b) => a.sortTime - b.sortTime);
});

const responseCoverageRows = computed(() => {
  if (!questionsTableRows.value.length) return [];
  const answered = questionsTableRows.value.filter((row) => String(row.responded_by || "").trim()).length;
  const unanswered = Math.max(questionsTableRows.value.length - answered, 0);
  return [
    { status: "Answered", count: answered },
    { status: "Unanswered", count: unanswered },
  ];
});

const responseCoverageCards = computed(() => {
  const answered = responseCoverageRows.value.find((row) => row.status === "Answered")?.count || 0;
  const unanswered = responseCoverageRows.value.find((row) => row.status === "Unanswered")?.count || 0;
  const total = answered + unanswered;
  const coverage = total > 0 ? (answered / total) * 100 : 0;
  return [
    { label: "Answered", value: String(answered) },
    { label: "Unanswered", value: String(unanswered) },
    { label: "Coverage Rate", value: `${coverage.toFixed(1)}%` },
  ];
});

const reactionMomentsRows = computed(() =>
  reactionMomentsRowsRaw.value.map((row) => ({
    progress_window: row.progress_window,
    chat_messages: Number(row.chat_messages) || 0,
    question_count: Number(row.question_count) || 0,
    excerpt: row.excerpt,
  }))
);

const tabs = [
  { id: "chat", label: "Chat" },
  { id: "questions", label: "Questions" },
  { id: "contributors", label: "Top Contributors" },
  { id: "activity", label: "Activity Over Time" },
  { id: "coverage", label: "Question Response Coverage" },
];

const hasChatData = computed(() => chatRows.value.length > 0);
const hasQuestionData = computed(() => questionRows.value.length > 0);
const missingAudienceSignalMessage = computed(() => {
  if (hasChatData.value && hasQuestionData.value) return "";
  if (!hasChatData.value && !hasQuestionData.value) {
    return "";
  }
  if (!hasChatData.value) {
    return "";
  }
  return "";
});

const questionsHelperMessage = computed(() => {
  if (hasQuestionData.value) return "";
  return "This session has no submitted questions.";
});
</script>

<template>
  <section class="page-section">
    <h2>Chat &amp; Questions</h2>
    <p class="page-description">Audience messages, submitted questions, and engagement activity over time.</p>

    <template v-if="state.workspace">
      <section v-if="missingAudienceSignalMessage" class="panel helper-panel">
        <p class="helper-text">{{ missingAudienceSignalMessage }}</p>
      </section>

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

      <template v-if="activeTab === 'chat'">
        <section v-if="!hasChatData" class="panel helper-panel">
          <p class="helper-text">This session has no chat messages.</p>
        </section>

        <div class="panel-heading panel-heading-inline table-toolbar-panel" v-if="chatTableRows.length">
          <div class="panel-heading-inline-title">
            <h3>Chat</h3>
            <button class="inline-icon-button" type="button" title="Download chat CSV" @click="downloadCsv('chat-messages.csv', chatTableRows)">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M12 3v10.2l3.6-3.6 1.4 1.4-6 6-6-6 1.4-1.4 3.6 3.6V3H12zm-7 14h14v2H5v-2z" fill="currentColor" />
              </svg>
            </button>
          </div>
        </div>

        <DataTable
          :rows="chatTableRows"
          :column-labels="chatColumnLabels"
          :column-widths="chatColumnWidths"
          csv-filename="chat-messages.csv"
          :show-toolbar="false"
        />
      </template>

      <template v-else-if="activeTab === 'questions'">
        <section v-if="questionsHelperMessage" class="panel helper-panel">
          <p class="helper-text">{{ questionsHelperMessage }}</p>
        </section>

        <div class="panel-heading panel-heading-inline table-toolbar-panel" v-if="questionsTableRows.length">
          <div class="panel-heading-inline-title">
            <h3>Questions</h3>
            <button class="inline-icon-button" type="button" title="Download questions CSV" @click="downloadCsv('session-questions.csv', questionsTableRows)">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M12 3v10.2l3.6-3.6 1.4 1.4-6 6-6-6 1.4-1.4 3.6 3.6V3H12zm-7 14h14v2H5v-2z" fill="currentColor" />
              </svg>
            </button>
          </div>
        </div>

        <DataTable
          :rows="questionsTableRows"
          :column-labels="questionsColumnLabels"
          :column-widths="questionsColumnWidths"
          csv-filename="session-questions.csv"
          :show-toolbar="false"
        />
      </template>

      <template v-else-if="activeTab === 'contributors'">
        <ContributorsComparisonChartCard
          title="Top Contributors (Messages vs Questions)"
          :rows="contributorsComparisonRows"
        />
      </template>

      <template v-else-if="activeTab === 'activity'">
        <ActivityTimelineChartCard
          title="Activity Over Time (UTC)"
          :rows="activityTimelineRows"
        />
      </template>

      <template v-else>
        <div class="metric-grid transcript-metric-grid" v-if="responseCoverageCards.length">
          <article v-for="card in responseCoverageCards" :key="card.label" class="metric-card metric-card-hero transcript-metric-card">
            <span class="metric-label">{{ card.label }}</span>
            <strong class="metric-value">{{ card.value }}</strong>
          </article>
        </div>

        <BarChartCard
          v-if="responseCoverageRows.length"
          title="Question Response Coverage"
          description="How many submitted questions received a response."
          :rows="responseCoverageRows"
          label-key="status"
          value-key="count"
        />
      </template>
    </template>
  </section>
</template>
