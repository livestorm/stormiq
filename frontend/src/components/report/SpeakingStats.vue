<script setup>
import { computed } from "vue";
import Collapsible from "../ui/Collapsible.vue";
import StatCard from "../ui/StatCard.vue";
import StatusWord from "../ui/StatusWord.vue";
import { speakingStats } from "../../transcript/stats";

// The Transcript tab's "Speaking stats": the pace, the longest stretch and
// the talk time per speaker, as the server measured them (docs/product.md
// §13). Collapsed by default; a hidden number says why.
const props = defineProps({
  measures: { type: Object, default: () => ({}) },
  speakers: { type: Array, default: () => [] },
});

const stats = computed(() => speakingStats(props.measures, props.speakers));
</script>

<template>
  <Collapsible title="Speaking stats" icon="timer" :takeaway="stats.summary" :chip="stats.chip">
    <div class="ss">
      <div class="ss-stats">
        <StatCard
          icon="activity"
          label="Pace"
          :value="stats.pace.text ? stats.pace.text.split(' ')[0] : '—'"
          :unit="stats.pace.text ? 'words a minute' : ''"
          :sub="stats.pace.why || ''"
          :status="stats.pace.status || ''"
          info="Words per minute of speech, over the time someone was talking."
        />
        <StatCard
          icon="timer"
          label="Longest stretch"
          :value="stats.stretch.text || '—'"
          :sub="stats.stretch.who ? `by ${stats.stretch.who}` : stats.stretch.why || ''"
          :status="stats.stretch.status || ''"
          info="The longest time one speaker talked, their lines joined across pauses of up to 2 seconds."
        />
      </div>

      <div v-if="stats.speakers.length" class="ss-table-wrap">
        <table class="ss-table">
          <caption class="ss-caption">
            Talk time per speaker
            <StatusWord v-if="stats.estimated" status="estimated" class="ss-status" />
          </caption>
          <thead>
            <tr>
              <th scope="col">Speaker</th>
              <th scope="col">Talk time</th>
              <th scope="col" class="ss-num">Share</th>
              <th scope="col" class="ss-num">Pace</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in stats.speakers" :key="row.key">
              <th scope="row" class="ss-name">{{ row.name }}</th>
              <td>
                <span class="ss-time">{{ row.time }}</span>
                <span class="ss-bar" aria-hidden="true"><span :style="{ width: `${row.bar}%` }" /></span>
              </td>
              <td class="ss-num">{{ row.share }}</td>
              <td class="ss-num">{{ row.pace }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="ss-why">Talk time per speaker: {{ stats.speakersWhy }}</p>
    </div>
  </Collapsible>
</template>

<style scoped>
.ss {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-4);
}

.ss-stats {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--siq-space-4);
}

.ss-table-wrap {
  overflow-x: auto;
}

.ss-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-ink);
}

.ss-caption {
  caption-side: top;
  padding-bottom: var(--siq-space-2);
  text-align: left;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink-2);
}

.ss-status {
  margin-left: var(--siq-space-2);
}

.ss-table th,
.ss-table td {
  padding: var(--siq-space-2) var(--siq-space-3) var(--siq-space-2) 0;
  text-align: left;
  vertical-align: middle;
  box-shadow: inset 0 -1px 0 var(--siq-hairline-row);
}

.ss-table thead th {
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-muted);
}

.ss-name {
  font-weight: var(--siq-weight-medium);
}

.ss-table .ss-num {
  text-align: right;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.ss-time {
  display: inline-block;
  min-width: 6.5rem;
  font-variant-numeric: tabular-nums;
}

.ss-bar {
  display: inline-block;
  width: 8rem;
  height: 8px;
  vertical-align: middle;
  border-radius: 999px;
  background: var(--siq-hairline);
  overflow: hidden;
}

.ss-bar > span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--siq-s1);
}

.ss-why {
  margin: 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
}

@media (max-width: 720px) {
  .ss-stats {
    grid-template-columns: minmax(0, 1fr);
  }
  .ss-bar {
    display: none;
  }
  .ss-time {
    min-width: 0;
  }
}

@media (forced-colors: active) {
  .ss-bar {
    border: 1px solid CanvasText;
  }
  .ss-bar > span {
    background: CanvasText;
  }
}
</style>
