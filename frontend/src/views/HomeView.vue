<script setup>
import { onMounted } from "vue";
import { useRouter } from "vue-router";
import { useWorkspace } from "../store/workspace";

// `/`, the site's home. Signed out, the shell shows the landing page here
// (`App.vue`, `design/landing.js`), so the site's address is its home page.
// Signed in, this view is what renders, and it moves on to Sessions.
const { state } = useWorkspace();
const router = useRouter();

onMounted(() => {
  if (state.auth.connectedUser || state.auth.allowLocalApiKeyFallback) router.replace("/sessions");
});
</script>

<template>
  <p class="siq-sr-only" role="status">Opening your sessions…</p>
</template>
