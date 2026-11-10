<script setup>
import { computed, defineAsyncComponent, onMounted, ref } from "vue";
import { RouterLink, RouterView, useRoute, useRouter } from "vue-router";
import { api, onUnauthorized } from "./api";
import TopBar from "./components/ui/TopBar.vue";
import { CALLBACK_PATH, afterCallback } from "./auth/callback";
import { showsLanding } from "./design/landing";
import { useWorkspace } from "./store/workspace";

// Every session page is a v1 page that loads its own data (the Report,
// Transcript and Follow-up views); old URLs are redirects in router.js. The
// shell keeps the dark frame for the one page not rebuilt yet (Admin).

const {
  state,
  applyBootstrap,
  resetWorkspace,
} = useWorkspace();
const route = useRoute();
const router = useRouter();
// Treated as "logged in" for nav-enable purposes. Local API-key mode
// counts (development convenience); blank state shows the Connect
// flow as the only interactive control.
const isAuthed = computed(
  () => Boolean(state.auth?.connectedUser || state.auth?.allowLocalApiKeyFallback),
);

const ADD_SESSION_PATH = "/sessions/add";
const isOnSessions = computed(
  () => route.path === "/sessions" || (route.path.startsWith("/sessions/") && route.path !== ADD_SESSION_PATH),
);
const isLocalApiKeyMode = computed(() => Boolean(state.auth?.allowLocalApiKeyFallback && state.apiKey));
const isOAuthMode = computed(() => !isLocalApiKeyMode.value && Boolean(state.auth?.oauthEnabled || state.auth?.connectedUser));
// ── v1 shell ──────────────────────────────────────────────────────────────
// Signed out, the v1 shell shows the landing page (`LandingView`) in place
// of any page but the public ones, and the top bar keeps only the lockup,
// the theme toggle and "Connect with Livestorm". The URL stays, so
// connecting brings the visitor back to the page they opened (a shared
// session link keeps working). Nothing shows until the bootstrap has said
// who is signed in, so a signed-in user never sees the landing flash.
const LandingView = defineAsyncComponent(() => import("./views/LandingView.vue"));
const authReady = ref(false);
const showLanding = computed(() => showsLanding({ authReady: authReady.value, authed: isAuthed.value, path: route.path }));

// Top-bar navigation, mapped onto the existing routes until the rebuilt
// screens land: "Sessions" is the session list (and every session page),
// "Add session" is Search.
const v1NavItems = computed(() => {
  if (!isAuthed.value) return [];
  const items = [
    {
      to: "/sessions",
      label: "Sessions",
      active: isOnSessions.value,
      // "page" on the list itself; "true" (the current section) on a session's pages.
      current: route.path === "/sessions" ? "page" : isOnSessions.value ? "true" : undefined,
      disabled: !isAuthed.value,
    },
    { to: ADD_SESSION_PATH, label: "Add session", icon: "plus", active: route.path === ADD_SESSION_PATH, current: route.path === ADD_SESSION_PATH ? "page" : undefined, disabled: !isAuthed.value },
  ];
  if (state.auth?.isAdmin) items.push({ to: "/admin", label: "Admin", active: route.path === "/admin" });
  return items;
});

const v1Account = computed(() => {
  const user = state.auth?.connectedUser;
  if (!user) return null;
  const name = user.fullName || user.email || "Connected";
  const detail = user.organizationName && user.organizationName !== name ? user.organizationName : "";
  return { name, detail };
});

// Pages built for the v1 design (route meta `v1Native`) fill the page; every
// other view keeps its classic dark styling inside a framed area until it is
// rebuilt.
const isV1NativeRoute = computed(() => Boolean(route.meta?.v1Native));

onMounted(async () => {
  try {
    const bootstrap = await api.bootstrap();
    applyBootstrap(bootstrap);
    // The OAuth return page. Read the browser's own address, not `route`:
    // the router may not have resolved its first route yet (the page's
    // chunk can still be loading when the bootstrap answers), and `route`
    // then still says "/", which left users on the "Connecting" screen.
    if (window.location.pathname === CALLBACK_PATH) {
      // Full page navigation (not router.replace) so the browser fetches
      // fresh index.html: a session that started on an old cached bundle
      // would otherwise keep running it after OAuth.
      window.location.replace(afterCallback(window.location.search));
      return;
    }
    // Local dev: LS_API_KEY is set but no OAuth session exists yet.
    // Auto-seed the identity from /me so the user lands logged in
    // without having to manually visit /api/dev/auto-login or go
    // through an OAuth flow that redirects to production.
    if (bootstrap?.auth?.allowLocalApiKeyFallback && !bootstrap?.auth?.connectedUser) {
      window.location.replace("/api/dev/auto-login");
      return;
    }
  } catch (error) {
    console.error("Bootstrap failed", error);
    state.error = "StormIQ could not load your account. Check your connection and reload the page.";
  } finally {
    authReady.value = true;
  }
});

// Any 401 means the Livestorm session expired or was revoked (or the
// visitor never connected): drop the signed-in state and ask them to
// connect. The page stays where it is, so the sidebar's Connect button
// brings them back to it (a shared session link keeps working).
onUnauthorized(() => {
  const wasSignedIn = Boolean(state.auth.connectedUser);
  state.auth.connectedUser = null;
  state.apiKey = "";
  resetWorkspace();
  state.error = wasSignedIn
    ? "Your Livestorm session has expired. Please reconnect."
    : "Please connect with Livestorm to open this page.";
});

function handleConnectClick() {
  const returnTo = route.path || "/sessions";
  window.location.href = `/api/auth/livestorm/start?returnTo=${encodeURIComponent(returnTo)}`;
}

async function handleLogoutClick() {
  try {
    await api.logout();
  } catch (error) {
    // Clear local state anyway: the user asked to leave.
    console.warn("Logout request failed", error);
  }
  state.auth.connectedUser = null;
  state.apiKey = "";
  resetWorkspace();
  // Signed out: the home page, which is the landing page.
  router.push("/");
}

</script>

<template>
  <div class="siq-app">
    <a class="siq-skip siq-fx" href="#siq-main">Skip to content</a>
    <TopBar
      :items="v1NavItems"
      :account="v1Account"
      :can-connect="isOAuthMode"
      :local-mode="isLocalApiKeyMode"
      @connect="handleConnectClick"
      @logout="handleLogoutClick"
    />

    <main id="siq-main" class="siq-main" tabindex="-1">
      <p v-if="state.error" class="siq-alert" role="alert">{{ state.error }}</p>

      <p v-if="!authReady" class="siq-sr-only" role="status">Loading StormIQ…</p>
      <LandingView v-else-if="showLanding" :can-connect="isOAuthMode" @connect="handleConnectClick" />
      <RouterView v-else-if="isV1NativeRoute" />
      <template v-else>
        <!-- Not rebuilt yet: the older view, in its own dark frame. -->
        <div class="siq-classic-frame" data-colors-semantic="dark">
          <RouterView />
        </div>
      </template>
    </main>

    <footer class="siq-footer">
      <p>
        StormIQ is an early-access helper, not an official Livestorm product. Review outputs before relying on them.
        <RouterLink to="/beta-info" class="siq-footer-link siq-fx">Read more</RouterLink>
      </p>
    </footer>
  </div>

</template>

<style scoped>
/* ── The app shell ───────────────────────────────────────────────────────── */
.siq-app {
  min-height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--siq-page);
  color: var(--siq-ink);
  font-family: var(--siq-font);
}

.siq-skip {
  position: absolute;
  left: var(--siq-space-4);
  top: -100px;
  z-index: 50;
  padding: var(--siq-space-2) var(--siq-space-4);
  border-radius: var(--siq-radius-control);
  background: var(--siq-card);
  color: var(--siq-accent);
}

.siq-skip:focus {
  top: var(--siq-space-2);
}

.siq-main {
  flex: 1 0 auto;
  width: 100%;
  max-width: var(--siq-container);
  margin: 0 auto;
  padding: var(--siq-space-8) var(--siq-gutter) var(--siq-space-12);
  box-sizing: border-box;
  min-width: 0;
}

.siq-main:focus {
  outline: none;
}

.siq-alert {
  margin: 0 0 var(--siq-space-6);
  padding: var(--siq-space-3) var(--siq-space-4);
  border-radius: var(--siq-radius-control);
  background: var(--siq-low-tint);
  color: var(--siq-low);
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
}

/* Classic views keep their dark styling until each is rebuilt. */
.siq-classic-frame {
  color-scheme: dark;
  padding: var(--siq-space-6);
  border-radius: var(--siq-radius-card);
  background: var(--color-surface-neutral-100);
  color: var(--color-text-neutral-base);
  font-family: "Object Sans", Inter, system-ui, sans-serif;
  min-width: 0;
}

.siq-footer {
  box-shadow: inset 0 1px 0 var(--siq-hairline);
}

.siq-footer p {
  max-width: var(--siq-container);
  margin: 0 auto;
  padding: var(--siq-space-5) var(--siq-gutter);
  box-sizing: border-box;
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  color: var(--siq-muted);
}

.siq-footer-link {
  color: var(--siq-accent);
  text-decoration: underline;
  text-underline-offset: 2px;
  border-radius: var(--siq-radius-inline);
}

.siq-footer-link:hover {
  color: var(--siq-accent-hover);
}

@media (max-width: 720px) {
  .siq-main {
    padding: var(--siq-space-6) var(--siq-space-4) var(--siq-space-8);
  }

  .siq-classic-frame {
    padding: var(--siq-space-4);
  }

  .siq-footer p {
    padding: var(--siq-space-4);
  }
}
</style>
