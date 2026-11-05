<script setup>
import { computed } from "vue";
import { useTheme } from "../../design/useTheme";
import UiButton from "./UiButton.vue";

// Light / dark toggle for the top bar. The page follows the system until the
// user clicks; the click saves an explicit choice (src/design/theme.js). The
// label names the action, so it also tells screen readers the current state.
const { theme, toggle } = useTheme();
const isDark = computed(() => theme.value === "dark");
const label = computed(() => (isDark.value ? "Switch to light mode" : "Switch to dark mode"));
</script>

<template>
  <UiButton variant="icon" :icon="isDark ? 'sun' : 'moon'" :icon-size="16" :icon-stroke="1.5" :label="label" @click="toggle" />
</template>
