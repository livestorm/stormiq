<script setup>
defineProps({
  title: {
    type: String,
    default: "",
  },
  body: {
    type: String,
    default: "",
  },
});
</script>

<template>
  <section class="panel">
    <h3 v-if="title">{{ title }}</h3>
    <pre class="markdown-block">{{ body || "No content available yet." }}</pre>
  </section>
</template>
