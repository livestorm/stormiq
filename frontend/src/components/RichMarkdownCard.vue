<script setup>
import { computed } from "vue";

const props = defineProps({
  body: {
    type: String,
    default: "",
  },
  emptyMessage: {
    type: String,
    default: "No content available yet.",
  },
});

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function renderInlineMarkdown(text) {
  return escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/`(.+?)`/g, "<code>$1</code>");
}

function isInlineSubheading(line) {
  const normalized = String(line || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .trim()
    .toLowerCase()
    .replace(/^[-*•\s]+/, "")
    .replace(/^[:*_`#\s]+/, "")
    .replace(/[:*_`#\s]+$/g, "");

  const normalizedNoPunct = normalized
    .replace(/[!?.,;]+$/g, "")
    .replace(/^["'([{]+/, "")
    .replace(/["')\]}]+$/g, "")
    .trim();

  return [
    "next session",
    "follow-up",
    "optional improvements",
    "prochaine session",
    "suivi",
    "ameliorations optionnelles",
  ].includes(normalizedNoPunct);
}

function cleanInlineSubheading(line) {
  return String(line || "")
    .trim()
    .replace(/^[-*•]\s+/, "")
    .replace(/^#+\s+/, "")
    .replace(/^(\*\*|__)(.+?)\1$/, "$2")
    .replace(/^(\*|_)(.+?)\1$/, "$2")
    .trim();
}

const renderedHtml = computed(() => {
  const source = String(props.body || "").trim();
  if (!source) {
    return `<p>${escapeHtml(props.emptyMessage)}</p>`;
  }

  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const blocks = [];
  let listItems = [];
  let orderedItems = [];
  let paragraphLines = [];

  const flushList = () => {
    if (!listItems.length) return;
    const renderedItems = [];
    for (const item of listItems) {
      if (isInlineSubheading(item)) {
        if (renderedItems.length) {
          blocks.push(`<ul>${renderedItems.join("")}</ul>`);
          renderedItems.length = 0;
        }
        blocks.push(`<h4>${renderInlineMarkdown(cleanInlineSubheading(item))}</h4>`);
        continue;
      }
      renderedItems.push(`<li>${renderInlineMarkdown(item)}</li>`);
    }
    if (renderedItems.length) {
      blocks.push(`<ul>${renderedItems.join("")}</ul>`);
    }
    listItems = [];
  };

  const flushOrderedList = () => {
    if (!orderedItems.length) return;
    blocks.push(`<ol>${orderedItems.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</ol>`);
    orderedItems = [];
  };

  const flushParagraph = () => {
    if (!paragraphLines.length) return;
    blocks.push(`<p>${renderInlineMarkdown(paragraphLines.join(" "))}</p>`);
    paragraphLines = [];
  };

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) {
      flushList();
      flushOrderedList();
      flushParagraph();
      continue;
    }

    const headingMatch = line.match(/^(#{1,3})\s+(.+)$/);
    if (headingMatch) {
      flushList();
      flushOrderedList();
      flushParagraph();
      const level = Math.min(headingMatch[1].length + 1, 4);
      blocks.push(`<h${level}>${renderInlineMarkdown(headingMatch[2])}</h${level}>`);
      continue;
    }

    const orderedMatch = line.match(/^\d+\.\s+(.+)$/);
    const bulletMatch = line.match(/^[-*•]\s+(.+)$/);
    if (isInlineSubheading(line)) {
      flushList();
      flushOrderedList();
      flushParagraph();
      blocks.push(`<h4>${renderInlineMarkdown(cleanInlineSubheading(line))}</h4>`);
      continue;
    }

    if (orderedMatch) {
      flushParagraph();
      flushList();
      orderedItems.push(orderedMatch[1] || "");
      continue;
    }

    if (bulletMatch) {
      flushParagraph();
      flushOrderedList();
      listItems.push(bulletMatch[1] || "");
      continue;
    }

    flushList();
    flushOrderedList();
    paragraphLines.push(line);
  }

  flushList();
  flushOrderedList();
  flushParagraph();
  return blocks.join("");
});
</script>

<template>
  <section class="panel rich-markdown-card">
    <article class="rich-markdown-content" v-html="renderedHtml"></article>
  </section>
</template>
