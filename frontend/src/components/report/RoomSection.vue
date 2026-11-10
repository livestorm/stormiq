<script setup>
import { computed, ref } from "vue";
import Collapsible from "../ui/Collapsible.vue";
import Icon from "../ui/Icon.vue";
import AudienceBlock from "./AudienceBlock.vue";
import ChatMessageList from "./ChatMessageList.vue";
import { chatThemes, roomSummary, themeRowName } from "../../report/room";

// The Report's "The room" section: open by default. For now it holds the
// chat themes: one row per theme with its messages, share and languages,
// and each row opens its messages, so the analysis's sorting can be checked.
// The wait gap, chapters, monologue and friction join it with 1e and 1f.

const props = defineProps({
  measures: { type: Object, required: true },
  // The report's `state.chat` and its loader.
  chat: { type: Object, required: true },
});
const emit = defineEmits(["load-chat"]);

const summary = computed(() => roomSummary(props.measures));
const themes = computed(() => chatThemes(props.measures["m.chat_themes"]));
const opened = ref(new Set());

// One name per row for screen readers (`themeRowName`); the visible cells
// are hidden from them so the row isn't read as fragments.
const rowName = themeRowName;

function toggle(key) {
  const next = new Set(opened.value);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  opened.value = next;
}
</script>

<template>
  <Collapsible title="The room" icon="message-circle" :takeaway="summary.takeaway" :chip="summary.chip" :open="true">
    <AudienceBlock v-if="themes" icon="list" title="What the chat was about" :status="themes.status" :info="themes.info">
      <div v-if="themes.rows.length" class="r-themes">
        <div class="r-themes-head" aria-hidden="true">
          <span>Theme</span>
          <span>Messages</span>
          <span>Share</span>
          <span>Languages</span>
        </div>
        <ul class="r-themes-list" aria-label="Audience chat messages by theme">
          <li v-for="row in themes.rows" :key="row.key" class="r-theme">
            <button
              type="button"
              class="r-theme-row siq-fx"
              :aria-label="rowName(row)"
              :aria-expanded="opened.has(row.key) ? 'true' : 'false'"
              :aria-controls="opened.has(row.key) ? `r-theme-${row.key}` : undefined"
              @click="toggle(row.key)"
            >
              <span class="r-theme-label" aria-hidden="true">{{ row.label }}</span>
              <span class="r-theme-count" aria-hidden="true">{{ row.messages }}</span>
              <span class="r-theme-share" aria-hidden="true">
                <span v-if="row.bar != null" class="r-theme-bar"><span :style="{ width: `${row.bar}%` }" /></span>
                <span>{{ row.share }}</span>
              </span>
              <span class="r-theme-lang" aria-hidden="true">{{ row.languages }}</span>
              <Icon class="r-theme-chevron" :name="opened.has(row.key) ? 'chevron-up' : 'chevron-down'" :size="16" />
            </button>
            <div v-if="opened.has(row.key)" :id="`r-theme-${row.key}`">
              <ChatMessageList
                :ids="row.ids"
                :chat="chat"
                :label="`Messages sorted as ${row.label.toLowerCase()}`"
                @load="emit('load-chat')"
              />
            </div>
          </li>
        </ul>
      </div>
      <p v-if="themes.empty" class="r-note">{{ themes.empty }}</p>
      <p v-for="note in themes.notes" :key="note" class="r-note">{{ note }}</p>
    </AudienceBlock>
  </Collapsible>
</template>

<style scoped>
.r-note {
  margin: 0;
  max-width: 680px;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
}

.r-themes {
  --r-columns: minmax(10rem, 2fr) 5.5rem minmax(7rem, 1.2fr) minmax(6rem, 1fr) 16px;
  max-width: 880px;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-variant-numeric: tabular-nums;
}

.r-themes-head,
.r-theme-row {
  display: grid;
  grid-template-columns: var(--r-columns);
  align-items: center;
  gap: var(--siq-space-3);
}

.r-themes-head {
  padding: var(--siq-space-2) var(--siq-space-2) var(--siq-space-2) 0;
  color: var(--siq-muted);
  font-weight: var(--siq-weight-medium);
  box-shadow: inset 0 -1px 0 var(--siq-hairline-head);
}

.r-themes-head span:nth-child(2) {
  text-align: right;
}

.r-themes-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.r-theme {
  box-shadow: inset 0 -1px 0 var(--siq-hairline-row);
}

/* A v1 control sets its own font, colours and border: the classic
   `button` rules still load under v1. */
.r-theme-row {
  width: 100%;
  min-height: var(--siq-target);
  margin: 0;
  padding: var(--siq-space-2) var(--siq-space-2) var(--siq-space-2) 0;
  border: 0;
  border-radius: var(--siq-radius-inline);
  background: transparent;
  color: var(--siq-ink);
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition-property: background-color;
  transition-duration: var(--siq-duration);
  transition-timing-function: var(--siq-ease);
}

.r-theme-row:hover {
  background: var(--siq-card-hover);
}

.r-theme-label {
  color: var(--siq-ink);
}

.r-theme-count {
  text-align: right;
}

.r-theme-share {
  display: flex;
  align-items: center;
  gap: var(--siq-space-2);
  color: var(--siq-ink-2);
}

.r-theme-bar {
  flex: 1 1 auto;
  max-width: 5rem;
  height: 6px;
  border-radius: 3px;
  background: var(--siq-surface-2);
  overflow: hidden;
}

.r-theme-bar span {
  display: block;
  height: 100%;
  background: var(--siq-s1);
}

.r-theme-lang {
  color: var(--siq-ink-2);
}

.r-theme-chevron {
  color: var(--siq-muted);
}

@media (max-width: 640px) {
  .r-themes-head {
    display: none;
  }
  .r-theme-row {
    grid-template-columns: minmax(0, 1fr) auto 16px;
    grid-template-areas:
      "label count chevron"
      "share share share"
      "lang lang lang";
    row-gap: var(--siq-space-1);
  }
  .r-theme-label {
    grid-area: label;
  }
  .r-theme-count {
    grid-area: count;
  }
  .r-theme-share {
    grid-area: share;
  }
  .r-theme-lang {
    grid-area: lang;
    color: var(--siq-muted);
  }
  .r-theme-chevron {
    grid-area: chevron;
  }
}

@media (forced-colors: active) {
  .r-theme {
    border-bottom: 1px solid CanvasText;
  }
  .r-theme-bar span {
    background: CanvasText;
  }
}

@media (prefers-reduced-motion: reduce) {
  .r-theme-row {
    transition: none;
  }
}
</style>
