<script setup>
import { computed } from "vue";
import { markdownBlocks } from "../../followup/markdown";

// An AI output's Markdown, rendered as text nodes (never v-html): model text
// can hold anything. Its headings sit under the page's own: a "#" becomes
// the `top` level (3 by default), and deeper ones follow, down to h6.
const props = defineProps({
  text: { type: String, default: "" },
  top: { type: Number, default: 3 },
});

const blocks = computed(() => markdownBlocks(props.text));

function tag(level) {
  return `h${Math.min(6, props.top + level - 1)}`;
}
</script>

<template>
  <div class="md">
    <template v-for="(block, index) in blocks" :key="index">
      <component :is="tag(block.level)" v-if="block.type === 'heading'" class="md-h" :class="`md-h${Math.min(block.level, 3)}`">
        <template v-for="(part, p) in block.parts" :key="p">
          <strong v-if="part.bold">{{ part.text }}</strong><em v-else-if="part.italic">{{ part.text }}</em
          ><code v-else-if="part.code">{{ part.text }}</code><template v-else>{{ part.text }}</template>
        </template>
      </component>
      <component :is="block.ordered ? 'ol' : 'ul'" v-else-if="block.type === 'list'" class="md-list">
        <li v-for="(item, i) in block.items" :key="i">
          <template v-for="(part, p) in item" :key="p">
            <strong v-if="part.bold">{{ part.text }}</strong><em v-else-if="part.italic">{{ part.text }}</em
            ><code v-else-if="part.code">{{ part.text }}</code><template v-else>{{ part.text }}</template>
          </template>
        </li>
      </component>
      <p v-else class="md-p">
        <template v-for="(part, p) in block.parts" :key="p">
          <strong v-if="part.bold">{{ part.text }}</strong><em v-else-if="part.italic">{{ part.text }}</em
          ><code v-else-if="part.code">{{ part.text }}</code><template v-else>{{ part.text }}</template>
        </template>
      </p>
    </template>
  </div>
</template>

<style scoped>
.md {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-3);
  max-width: 72ch;
  color: var(--siq-ink);
  font-size: var(--siq-text-body);
  line-height: var(--siq-text-body-lh);
  overflow-wrap: anywhere;
}

.md-h {
  margin: var(--siq-space-3) 0 0;
  color: var(--siq-ink);
  font-weight: var(--siq-weight-medium);
}

.md-h:first-child {
  margin-top: 0;
}

.md-h1 {
  font-size: var(--siq-text-section);
  line-height: var(--siq-text-section-lh);
}

.md-h2 {
  font-size: var(--siq-text-card-title);
  line-height: var(--siq-text-card-title-lh);
}

.md-h3 {
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
}

.md-p {
  margin: 0;
  white-space: pre-line;
}

.md-list {
  margin: 0;
  padding-left: var(--siq-space-6);
}

.md-list li + li {
  margin-top: var(--siq-space-1);
}

.md code {
  padding: 0 4px;
  border-radius: var(--siq-radius-inline);
  background: var(--siq-hairline);
  font-size: 0.92em;
}

.md strong {
  font-weight: var(--siq-weight-medium);
}
</style>
