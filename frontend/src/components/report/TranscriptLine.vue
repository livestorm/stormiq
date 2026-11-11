<script setup>
import StatusWord from "../ui/StatusWord.vue";

// One line of the Transcript tab (`transcript/lines.js` words it): a line
// of speech, full width, or a chat, Q&A or answer line, indented with a rule
// in its series colour (chat `s1`, Q&A `s2`). `flat` drops the indent, for
// the room's own list. An unclear word is underlined, dotted, and says so to
// screen readers. A chapter line (`kind: "chapter"`) is a heading across the
// list. Text is rendered as text, never as HTML.
defineProps({
  line: { type: Object, required: true },
  flat: { type: Boolean, default: false },
});
</script>

<template>
  <li v-if="line.kind === 'chapter'" class="tl tl-chapter">
    <span class="tl-time">
      {{ line.time }}<span v-if="line.timeIsRecording && line.time" class="siq-sr-only"> into the recording</span>
    </span>
    <h3 class="tl-chapter-title"><span class="siq-sr-only">Chapter: </span>{{ line.title }}</h3>
  </li>
  <li
    v-else
    class="tl"
    :class="[line.kind === 'speech' ? 'tl-speech' : 'tl-room', `tl-${line.kind}`, { 'tl-flat': flat, 'tl-team': line.team }]"
  >
    <span class="tl-time">
      {{ line.time }}<span v-if="line.timeIsRecording" class="siq-sr-only"> into the recording</span>
    </span>
    <div class="tl-body">
      <p class="tl-who" :class="{ 'siq-sr-only': line.continued }">
        <span class="tl-name">{{ line.who }}</span>
        <span v-if="line.kind !== 'speech'" class="tl-source"> · {{ line.source }}</span>
        <StatusWord v-if="line.status === 'Answered in writing'" status="measured" :label="line.status" class="tl-status" />
      </p>
      <p class="tl-text">
        <template v-for="(part, index) in line.parts" :key="index">
          <span v-if="part.unclear" class="tl-unclear">{{ part.text }}<span class="siq-sr-only"> (low confidence)</span></span>
          <template v-else>{{ part.text }}</template>
        </template>
      </p>
    </div>
  </li>
</template>

<style scoped>
.tl {
  display: grid;
  grid-template-columns: 4.5rem minmax(0, 1fr);
  gap: var(--siq-space-3);
  padding: var(--siq-space-2) var(--siq-space-6);
}

.tl-chapter {
  margin-top: var(--siq-space-2);
  padding-top: var(--siq-space-4);
  padding-bottom: var(--siq-space-2);
  box-shadow: inset 0 1px 0 var(--siq-hairline);
}

.tl-chapter:first-child {
  margin-top: 0;
  box-shadow: none;
}

.tl-chapter-title {
  margin: 0;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  font-weight: var(--siq-weight-semibold);
  color: var(--siq-ink);
  overflow-wrap: anywhere;
}

.tl-time {
  padding-top: 2px;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.tl-body {
  min-width: 0;
}

.tl-who {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0 var(--siq-space-2);
  margin: 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
}

.tl-name {
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink);
}

.tl-source {
  color: var(--siq-muted);
}

.tl-text {
  margin: 0;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-ink);
  overflow-wrap: anywhere;
  white-space: pre-line;
}

.tl-speech .tl-text {
  font-size: var(--siq-text-body);
  line-height: var(--siq-text-body-lh);
}

/* The room's lines: indented, with a rule in their series colour. */
.tl-room .tl-body {
  margin-left: 48px;
  padding-left: var(--siq-space-3);
  box-shadow: inset 2px 0 0 var(--siq-s1);
}

.tl-question .tl-body,
.tl-answer .tl-body {
  box-shadow: inset 2px 0 0 var(--siq-s2);
}

.tl-room .tl-name {
  color: var(--siq-ink-2);
}

.tl-team .tl-name {
  color: var(--siq-ink);
}

.tl-flat .tl-body {
  margin-left: 0;
}

.tl-unclear {
  text-decoration: underline dotted var(--siq-fair);
  text-decoration-thickness: 2px;
  text-underline-offset: 3px;
}

@media (max-width: 720px) {
  .tl {
    grid-template-columns: minmax(0, 1fr);
    gap: 0;
    padding: var(--siq-space-2) var(--siq-space-4);
  }
  .tl-room .tl-body,
  .tl-room .tl-time {
    margin-left: var(--siq-space-4);
  }
  .tl-room .tl-time {
    padding-left: var(--siq-space-3);
  }
  .tl-flat .tl-body,
  .tl-flat .tl-time {
    margin-left: 0;
  }
}

@media (forced-colors: active) {
  .tl-room .tl-body {
    border-left: 2px solid CanvasText;
  }
  .tl-chapter {
    border-top: 1px solid CanvasText;
  }
}

@media print {
  .tl {
    break-inside: avoid;
  }
}
</style>
