<script setup>
import { computed } from "vue";
import Collapsible from "../ui/Collapsible.vue";
import AudienceBlock from "./AudienceBlock.vue";
import AudienceTable from "./AudienceTable.vue";
import {
  audienceSummary,
  channelTable,
  concentration,
  leadTable,
  roomMap,
  segmentTables,
  sessionShowUp,
} from "../../report/audience";
import { voicesView } from "../../report/voices";
import UiButton from "../ui/UiButton.vue";

// The Report's "The audience" section: a collapsible card with the room map,
// who spoke up (the people who wrote, by name, with one message each, then
// how concentrated the chat was), and the channel, segment and lead-time
// tables. Every number is a measure the server sent (`record.measures`);
// `report/audience.js` words them. A part whose measure is missing isn't
// shown.

const props = defineProps({
  measures: { type: Object, required: true },
  // The view's `createVoices().state`: {loading, error, body}.
  voices: { type: Object, required: true },
});
const emit = defineEmits(["retry-voices"]);

const summary = computed(() => audienceSummary(props.measures));
const room = computed(() => roomMap(props.measures["m.room_map"]));
const chat = computed(() => concentration(props.measures["m.chat_concentration"]));
const people = computed(() => voicesView(props.voices.body));
const channels = computed(() => channelTable(props.measures["m.channel_quality"]));
const segments = computed(() => segmentTables(props.measures["m.segment_quality"]));
const lead = computed(() => leadTable(props.measures["m.lead_time_show_up"]));
const reference = computed(() => sessionShowUp(props.measures));

const QUALITY_COLUMNS = [
  { key: "registrants", label: "Registrants" },
  { key: "share", label: "Share" },
  { key: "showUp", label: "Show-up" },
  { key: "wrote", label: "Wrote" },
  { key: "medianTime", label: "Median time" },
];

const LEAD_COLUMNS = [
  { key: "registrants", label: "Registrants" },
  { key: "share", label: "Share" },
  { key: "attendees", label: "Attendees" },
  { key: "showUp", label: "Show-up" },
];
</script>

<template>
  <Collapsible title="The audience" icon="users" :takeaway="summary.takeaway" :chip="summary.chip">
    <AudienceBlock v-if="room" icon="users" title="Who stayed, who wrote" :status="room.status" :info="room.info">
      <div class="r-room" role="list" aria-label="Audience attendees by whether they stayed and wrote">
        <div
          v-for="tile in room.tiles"
          :key="tile.key"
          class="r-tile"
          :class="{ 'is-emphasis': tile.emphasis }"
          role="listitem"
        >
          <span class="r-tile-number">{{ tile.people }}</span>
          <span class="r-tile-label">{{ tile.label }}</span>
          <span v-if="tile.share" class="r-tile-share">{{ tile.share }}</span>
        </div>
      </div>
      <p v-if="room.note" class="r-note">{{ room.note }}</p>
    </AudienceBlock>

    <AudienceBlock
      v-if="people || chat || voices.loading || voices.error"
      icon="message-circle"
      title="Who spoke up"
      :status="chat?.status || ''"
      :info="chat?.info || ''"
    >
      <p v-if="voices.loading && !people" class="r-note" role="status">Loading who wrote…</p>
      <div v-else-if="voices.error && !people" class="r-retry" role="alert">
        <p class="r-note">{{ voices.error }}</p>
        <UiButton variant="secondary" small icon="refresh-cw" @click="emit('retry-voices')">Try again</UiButton>
      </div>
      <template v-else-if="people">
        <p v-if="people.lead" class="r-lead-line">{{ people.lead }}</p>
        <p v-if="people.empty" class="r-note">{{ people.empty }}</p>
        <ol v-if="people.rows.length" class="r-voices" aria-label="The audience members who wrote the most">
          <li v-for="row in people.rows" :key="row.key" class="r-voice">
            <div class="r-voice-head">
              <span class="r-voice-name" :class="{ 'is-unnamed': !row.named }">{{ row.name }}</span>
              <span v-if="row.company" class="r-voice-company">{{ row.company }}</span>
              <span class="r-voice-counts">{{ row.counts }}</span>
            </div>
            <div class="r-voice-bar" aria-hidden="true"><span :style="{ width: row.width }" /></div>
            <p v-if="row.quote" class="r-voice-quote">
              <span v-if="row.time" class="r-voice-time">{{ row.time }}</span>
              <span>“{{ row.quote }}”</span>
            </p>
          </li>
        </ol>
        <p v-if="people.others" class="r-note">{{ people.others }}</p>
      </template>
      <p v-if="chat?.sentence" class="r-note">How spread out the chat was: {{ chat.value }} {{ chat.sentence }}</p>
      <p v-if="chat?.note" class="r-note">{{ chat.note }}</p>
      <p v-if="people?.unnamed" class="r-note">{{ people.unnamed }}</p>
    </AudienceBlock>

    <AudienceBlock v-if="channels" icon="share-2" title="Where they came from" :status="channels.status" :info="channels.info">
      <AudienceTable
        v-if="channels.rows.length"
        caption="Audience registrants by channel"
        label-heading="Channel"
        noun="channels"
        :columns="QUALITY_COLUMNS"
        :rows="channels.rows"
      />
      <p v-if="channels.empty" class="r-note">{{ channels.empty }}</p>
      <p v-for="note in channels.notes" :key="note" class="r-note">{{ note }}</p>
      <p v-if="reference && channels.rows.length" class="r-note">This session's show-up: {{ reference }}.</p>
    </AudienceBlock>

    <AudienceBlock v-if="segments" icon="pie-chart" title="Registration answers" :status="segments.status" :info="segments.info">
      <div v-for="table in segments.tables" :key="table.field" class="r-field">
        <p class="r-field-name">{{ table.name }}</p>
        <p v-if="table.detail" class="r-note">{{ table.detail }}</p>
        <AudienceTable
          :caption="`Audience registrants by answer to “${table.name}”`"
          label-heading="Answer"
          noun="answers"
          :columns="QUALITY_COLUMNS"
          :rows="table.rows"
        />
        <p v-for="note in table.notes" :key="note" class="r-note">{{ note }}</p>
      </div>
      <p v-if="segments.empty" class="r-note">{{ segments.empty }}</p>
      <p v-if="segments.note" class="r-note">{{ segments.note }}</p>
    </AudienceBlock>

    <AudienceBlock v-if="lead" icon="calendar" title="When they registered" :status="lead.status" :info="lead.info">
      <AudienceTable
        v-if="lead.rows.length"
        caption="Show-up by how long before the start people registered"
        label-heading="Registered"
        :columns="LEAD_COLUMNS"
        :rows="lead.rows"
      />
      <p v-if="lead.empty" class="r-note">{{ lead.empty }}</p>
      <p v-for="note in lead.notes" :key="note" class="r-note">{{ note }}</p>
      <p v-if="reference && lead.rows.length" class="r-note">This session's show-up: {{ reference }}.</p>
    </AudienceBlock>
  </Collapsible>
</template>

<style scoped>
.r-note,
.r-field-name {
  margin: 0;
  max-width: 680px;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
}

.r-field {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-2);
}

.r-field + .r-field {
  margin-top: var(--siq-space-4);
}

.r-field-name {
  color: var(--siq-ink-2);
  font-weight: var(--siq-weight-medium);
}

.r-room {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--siq-space-3);
  max-width: 560px;
}

.r-tile {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--siq-space-4);
  border-radius: var(--siq-radius-control);
  box-shadow: inset 0 0 0 1px var(--siq-hairline);
}

/* The largest group (the header's sentence): an audience-coloured edge, not
   an outline, so it never reads as keyboard focus. */
.r-tile.is-emphasis {
  box-shadow:
    inset 4px 0 0 var(--siq-s1),
    inset 0 0 0 1px var(--siq-hairline);
}

.r-tile-number {
  font-size: var(--siq-text-headline);
  line-height: var(--siq-text-headline-lh);
  font-variant-numeric: tabular-nums;
  color: var(--siq-ink);
}

.r-tile-label {
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-ink-2);
}

.r-tile-share {
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  color: var(--siq-muted);
  font-variant-numeric: tabular-nums;
}

@media (forced-colors: active) {
  .r-tile {
    border: 1px solid CanvasText;
  }
  .r-tile.is-emphasis {
    border-left-width: 4px;
  }
}

.r-retry {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--siq-space-2);
}

.r-lead-line {
  margin: 0;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-ink-2);
}

.r-voices {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-3);
  max-width: 720px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.r-voice {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-1);
  min-width: 0;
}

.r-voice-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--siq-space-1) var(--siq-space-3);
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
}

.r-voice-name {
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink);
}

.r-voice-name.is-unnamed {
  font-weight: var(--siq-weight-regular);
  color: var(--siq-ink-2);
}

.r-voice-company,
.r-voice-counts {
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
}

.r-voice-counts {
  margin-left: auto;
  font-variant-numeric: tabular-nums;
}

.r-voice-bar {
  height: 4px;
  border-radius: var(--siq-radius-pill);
  background: var(--siq-input);
  overflow: hidden;
}

.r-voice-bar span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--siq-s1);
}

.r-voice-quote {
  display: -webkit-box;
  margin: 0;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-ink-2);
  overflow-wrap: anywhere;
}

.r-voice-time {
  margin-right: var(--siq-space-2);
  color: var(--siq-muted);
  font-variant-numeric: tabular-nums;
}

@media (forced-colors: active) {
  .r-voice-bar {
    border: 1px solid CanvasText;
  }
  .r-voice-bar span {
    background: CanvasText;
  }
}
</style>
