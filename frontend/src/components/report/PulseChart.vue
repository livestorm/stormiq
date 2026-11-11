<script setup>
import { computed, ref } from "vue";
import CardHeader from "../ui/CardHeader.vue";
import Chip from "../ui/Chip.vue";
import UiButton from "../ui/UiButton.vue";
import SessionTimeAxis from "./SessionTimeAxis.vue";
import { barPercent, busiestMinute, clockLabel, countTicks } from "../../report/chartGeometry";

// The Pulse (C1): minute by minute, when the audience wrote, asked and
// listened, on one time axis. Lanes from `record.pulse`: the audience's chat
// with the team's stacked on top, the questions, and the speech when the
// server says the transcript may be read against the chat
// (`record.pulse.speech.shown`). The clock chip only labels the alignment's
// status; it never decides what is shown. Every column is also in the table.

const props = defineProps({
  pulse: { type: Object, required: true },
  alignment: { type: Object, default: null },
  // What the findings mark (`report/findings.js` `pulseMarks`): the
  // monologue stretch found, and the prompts to the room.
  marks: { type: Object, default: () => ({ bands: [], prompts: [] }) },
});

const showTable = ref(false);

const minutes = computed(() => props.pulse?.minutes || []);
const count = computed(() => minutes.value.length);
const speechShown = computed(() => Boolean(props.pulse?.speech?.shown));

const chatAxis = computed(() =>
  countTicks(Math.max(0, ...minutes.value.map((row) => (row.audienceChat || 0) + (row.teamChat || 0)))),
);
const questionAxis = computed(() => countTicks(Math.max(0, ...minutes.value.map((row) => row.questions || 0)), { maxTicks: 2 }));

const columns = computed(() =>
  minutes.value.map((row) => {
    const audience = row.audienceChat || 0;
    const team = row.teamChat || 0;
    const questions = row.questions || 0;
    const parts = [`${audience} from the audience`, `${team} from your team`, `${questions} ${questions === 1 ? "question" : "questions"}`];
    if (speechShown.value) parts.push(`${Math.round(row.speakingS || 0)} s of speech`);
    return {
      minute: row.minute,
      audience: barPercent(audience, chatAxis.value.top),
      team: barPercent(team, chatAxis.value.top),
      questions: barPercent(questions, questionAxis.value.top),
      speech: barPercent(row.speakingS || 0, 60),
      title: `${clockLabel(row.minute * 60)} · ${parts.join(", ")}`,
    };
  }),
);

const gap = computed(() => (count.value <= 60 ? "2px" : count.value <= 150 ? "1px" : "0px"));

const description = computed(() => {
  const peak = busiestMinute(minutes.value, "audienceChat");
  const asked = busiestMinute(minutes.value, "questions");
  const parts = [`Audience chat messages and questions per minute over ${count.value} minutes`];
  parts.push(peak ? `the chat was busiest at ${clockLabel(peak.minute * 60)} with ${peak.count}` : "nobody in the audience wrote in the chat");
  if (asked) parts.push(`questions peaked at ${clockLabel(asked.minute * 60)} with ${asked.count}`);
  return `${parts.join("; ")}. The same numbers are in the table view.`;
});

function edgeText(edge, label) {
  const counts = edge?.counts || {};
  const chat = (counts.audienceChat || 0) + (counts.teamChat || 0);
  const questions = counts.questions || 0;
  if (!chat && !questions) return "";
  const parts = [];
  if (chat) parts.push(`${chat} chat ${chat === 1 ? "message" : "messages"}`);
  if (questions) parts.push(`${questions} ${questions === 1 ? "question" : "questions"}`);
  return `${label}: ${parts.join(", ")}`;
}

const edges = computed(() =>
  [edgeText(props.pulse?.edges?.before, "Before the start"), edgeText(props.pulse?.edges?.after, "After the end")].filter(Boolean),
);

const CLOCK = {
  verified: { word: "Clock checked", tone: "good", icon: "check" },
  assumed: { word: "Clock assumed", tone: "neutral", icon: "info" },
  misaligned: { word: "Clock doesn't match", tone: "fair", icon: "shield" },
};
const clock = computed(() => CLOCK[props.alignment?.status] || null);

const source = computed(() =>
  speechShown.value
    ? "Source: Livestorm chat and Q&A, and the session transcript, on the session clock. StormIQ analysis."
    : "Source: Livestorm chat and Q&A, on the session clock. StormIQ analysis.",
);
</script>

<template>
  <div class="r-pulse">
    <CardHeader icon="activity" title="When the room reacted" :level="2">
      <template #right>
        <Chip v-if="clock" :tone="clock.tone" :icon="clock.icon">{{ clock.word }}</Chip>
        <UiButton variant="ghost" small :icon="showTable ? 'activity' : 'table'" :aria-pressed="showTable ? 'true' : 'false'" @click="showTable = !showTable">
          {{ showTable ? "View as chart" : "View as table" }}
        </UiButton>
      </template>
    </CardHeader>

    <p v-if="!count" class="r-pulse-empty">This session has no live minutes to show: its start time is unknown.</p>

    <div v-else-if="!showTable" class="r-pulse-chart" role="img" :aria-label="description" :style="{ '--r-gap': gap }">
      <div class="r-lane-head">
        <span class="r-lane-title">Chat per minute</span>
        <span class="r-key"><span class="r-swatch r-s1" />Audience</span>
        <span class="r-key"><span class="r-swatch r-s3" />Your team</span>
        <span v-if="marks.prompts.length" class="r-key"><span class="r-prompt-key" aria-hidden="true" />Prompt to the room</span>
      </div>
      <div class="r-lane r-lane-chat">
        <span
          v-for="band in marks.bands"
          :key="`b${band.left}`"
          class="r-band"
          :style="{ left: `${band.left}%`, width: `${band.width}%` }"
          :title="band.label"
        ><span class="r-band-label">{{ band.label }}</span></span>
        <span
          v-for="tick in chatAxis.ticks"
          :key="`g${tick}`"
          class="r-grid"
          :style="{ bottom: `${(tick / chatAxis.top) * 100}%` }"
        ><span class="r-grid-label">{{ tick }}</span></span>
        <div class="r-columns">
          <div v-for="column in columns" :key="column.minute" class="r-column" :title="column.title">
            <span class="r-bar r-bar-team" :style="{ height: `${column.team}%` }" />
            <span class="r-bar r-bar-audience" :style="{ height: `${column.audience}%` }" />
          </div>
        </div>
      </div>

      <div v-if="marks.prompts.length" class="r-prompts">
        <span
          v-for="mark in marks.prompts"
          :key="`p${mark.left}`"
          class="r-prompt"
          :class="{ 'is-missed': mark.missed }"
          :style="{ left: `${mark.left}%` }"
          :title="mark.title"
        />
      </div>

      <div class="r-lane-head">
        <span class="r-lane-title">Questions per minute</span>
        <span class="r-key"><span class="r-swatch r-s2" />Q&amp;A</span>
      </div>
      <div class="r-lane r-lane-small">
        <div class="r-columns">
          <div v-for="column in columns" :key="column.minute" class="r-column" :title="column.title">
            <span class="r-bar r-bar-questions" :style="{ height: `${column.questions}%` }" />
          </div>
        </div>
      </div>

      <template v-if="speechShown">
        <div class="r-lane-head">
          <span class="r-lane-title">Speech, seconds per minute</span>
        </div>
        <div class="r-lane r-lane-small">
          <div class="r-columns">
            <div v-for="column in columns" :key="column.minute" class="r-column" :title="column.title">
              <span class="r-bar r-bar-speech" :style="{ height: `${column.speech}%` }" />
            </div>
          </div>
        </div>
      </template>

      <SessionTimeAxis :count="count" />
    </div>

    <div v-else class="r-pulse-table-wrap">
      <table class="r-pulse-table">
        <caption class="siq-sr-only">Chat, questions{{ speechShown ? " and speech" : "" }} per minute</caption>
        <thead>
          <tr>
            <th scope="col">Minute</th>
            <th scope="col">Audience chat</th>
            <th scope="col">Your team</th>
            <th scope="col">Questions</th>
            <th v-if="speechShown" scope="col">Speech (s)</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in minutes" :key="row.minute">
            <th scope="row">{{ clockLabel(row.minute * 60) }}</th>
            <td>{{ row.audienceChat }}</td>
            <td>{{ row.teamChat }}</td>
            <td>{{ row.questions }}</td>
            <td v-if="speechShown">{{ Math.round(row.speakingS || 0) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <p v-if="edges.length" class="r-pulse-note">{{ edges.join(" · ") }}</p>
    <p class="r-pulse-note">{{ source }}</p>
  </div>
</template>

<style scoped>
.r-pulse {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-3);
}

.r-pulse-chart {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-2);
  margin-top: var(--siq-space-2);
}

.r-lane-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--siq-space-2) var(--siq-space-4);
  margin-top: var(--siq-space-2);
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  color: var(--siq-muted);
}

.r-lane-title {
  color: var(--siq-ink-2);
  font-weight: var(--siq-weight-medium);
}

.r-key {
  display: inline-flex;
  align-items: center;
  gap: var(--siq-space-1);
}

.r-swatch {
  width: 10px;
  height: 10px;
  border-radius: 3px;
}

.r-s1 { background: var(--siq-s1); }
.r-s2 { background: var(--siq-s2); }
.r-s3 { background: var(--siq-s3); }

.r-lane {
  position: relative;
}

.r-band {
  position: absolute;
  top: 0;
  bottom: 0;
  z-index: 0;
  background: var(--siq-fair-tint);
  box-shadow: inset 1px 0 0 var(--siq-fair), inset -1px 0 0 var(--siq-fair);
  pointer-events: auto;
}

.r-band-label {
  position: absolute;
  top: var(--siq-space-1);
  left: var(--siq-space-2);
  right: var(--siq-space-2);
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  color: var(--siq-ink-2);
}

.r-prompts {
  position: relative;
  height: 10px;
}

.r-prompt,
.r-prompt-key {
  width: 0;
  height: 0;
  border-left: 5px solid transparent;
  border-right: 5px solid transparent;
  border-bottom: 8px solid var(--siq-nontext);
}

.r-prompt {
  position: absolute;
  top: 1px;
  transform: translateX(-50%);
}

.r-prompt.is-missed {
  border-bottom-color: var(--siq-fair);
}

.r-prompt-key {
  display: inline-block;
}

.r-lane-chat {
  height: 180px;
}

.r-lane-small {
  height: 40px;
  box-shadow: inset 0 -1px 0 var(--siq-hairline-row);
}

.r-grid {
  position: absolute;
  left: 0;
  right: 0;
  height: 0;
  box-shadow: inset 0 1px 0 var(--siq-hairline-row);
}

.r-grid-label {
  position: absolute;
  right: 100%;
  bottom: -8px;
  padding-right: var(--siq-space-2);
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  color: var(--siq-muted);
  font-variant-numeric: tabular-nums;
}

.r-columns {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: stretch;
  gap: var(--r-gap, 2px);
}

.r-column {
  flex: 1 1 0;
  min-width: 0;
  display: flex;
  flex-direction: column-reverse;
  justify-content: flex-start;
}

.r-bar {
  display: block;
  width: 100%;
}

.r-bar-audience {
  background: var(--siq-s1);
  border-radius: 0;
}

.r-bar-team {
  background: var(--siq-s3);
}

.r-column > .r-bar:last-child {
  border-radius: 3px 3px 0 0;
}

.r-bar-questions {
  background: var(--siq-s2);
}

.r-bar-speech {
  background: var(--siq-other);
}

.r-lane-chat {
  margin-left: var(--siq-space-6);
}

.r-lane-small,
.r-pulse-chart :deep(.r-axis) {
  margin-left: var(--siq-space-6);
}

.r-pulse-empty,
.r-pulse-note {
  margin: 0;
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  color: var(--siq-muted);
}

.r-pulse-empty {
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-ink-2);
}

.r-pulse-table-wrap {
  max-height: 420px;
  overflow: auto;
}

.r-pulse-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-variant-numeric: tabular-nums;
  color: var(--siq-ink);
}

.r-pulse-table th,
.r-pulse-table td {
  padding: var(--siq-space-2) var(--siq-space-3);
  text-align: right;
  box-shadow: inset 0 -1px 0 var(--siq-hairline-row);
}

.r-pulse-table thead th {
  position: sticky;
  top: 0;
  background: var(--siq-card);
  color: var(--siq-muted);
  font-weight: var(--siq-weight-medium);
  box-shadow: inset 0 -1px 0 var(--siq-hairline-head);
}

.r-pulse-table th[scope="row"],
.r-pulse-table thead th:first-child {
  text-align: left;
  font-weight: var(--siq-weight-regular);
}

@media (forced-colors: active) {
  .r-bar {
    background: CanvasText;
  }
  .r-lane-small {
    border-bottom: 1px solid CanvasText;
  }
}
</style>
