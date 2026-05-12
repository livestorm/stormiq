import { createRouter, createWebHistory } from "vue-router";
import HomeView from "./views/HomeView.vue";
import SearchView from "./views/SearchView.vue";
import SingleAnalysisListView from "./views/SingleAnalysisListView.vue";
import CrossAnalysisView from "./views/CrossAnalysisView.vue";
import SessionOverviewView from "./views/SessionOverviewView.vue";
import TranscriptView from "./views/TranscriptView.vue";
import ChatQuestionsView from "./views/ChatQuestionsView.vue";
import AnalysisView from "./views/AnalysisView.vue";
import SmartRecapView from "./views/SmartRecapView.vue";
import ContentRepurposingView from "./views/ContentRepurposingView.vue";
import AuthCallbackView from "./views/AuthCallbackView.vue";
import BetaNoticeView from "./views/BetaNoticeView.vue";
import { useWorkspace } from "./store/workspace";

// Phase 4 routing.
//
// Top-level navigation reflects the new product shape:
//   /search                                      — fetch sessions/events
//   /single-analysis                             — workspace card grid
//   /single-analysis/:sessionId/{tab}            — per-session analysis tabs
//   /cross-analysis                              — placeholder (Phase 3)
//
// Each session's analysis is now a shareable URL keyed by sessionId.
// Teammates can paste any /single-analysis/:sessionId/... link and
// (assuming they're in the same Livestorm org) land directly on the
// requested tab with the cached workspace already loaded.
//
// Legacy routes redirect when possible. If the route's :sessionId
// would be the store's active session, we synthesise the redirect via
// a function that reads the store. Otherwise we drop to the list view
// so the user can pick a session.

// Resolve the active session id from the workspace store — used by
// legacy-route redirects. The store is a singleton: useWorkspace()
// always returns the same reactive state, so reading it here doesn't
// require a Vue component context.
function resolveActiveSessionId() {
  try {
    return String(useWorkspace().state.workspace?.sessionId || "").trim();
  } catch (error) {
    return "";
  }
}

function redirectLegacyTab(tab) {
  return () => {
    const sessionId = resolveActiveSessionId();
    return sessionId
      ? { path: `/single-analysis/${sessionId}/${tab}` }
      : { path: "/single-analysis" };
  };
}

const routes = [
  // ── Top level ───────────────────────────────────────────────────────────
  { path: "/", redirect: "/single-analysis" },
  { path: "/search", component: SearchView },
  { path: "/single-analysis", component: SingleAnalysisListView },
  { path: "/cross-analysis", component: CrossAnalysisView },

  // ── Session detail (parameterised) ──────────────────────────────────────
  // The :sessionId is the URL contract for sharing. The store
  // synchronises state.workspace to whatever this segment carries.
  {
    path: "/single-analysis/:sessionId",
    redirect: (to) => ({ path: `/single-analysis/${to.params.sessionId}/session-overview` }),
  },
  { path: "/single-analysis/:sessionId/session-overview", component: SessionOverviewView, props: true },
  { path: "/single-analysis/:sessionId/transcript", component: TranscriptView, props: true },
  { path: "/single-analysis/:sessionId/chat-questions", component: ChatQuestionsView, props: true },
  { path: "/single-analysis/:sessionId/analysis", component: AnalysisView, props: true },
  { path: "/single-analysis/:sessionId/smart-recap", component: SmartRecapView, props: true },
  { path: "/single-analysis/:sessionId/content-repurposing", component: ContentRepurposingView, props: true },

  // ── Legacy redirects ────────────────────────────────────────────────────
  // Old bookmarks land on the new equivalent. When there's no active
  // session in the store (e.g. cold load), we fall through to the list.
  { path: "/events", redirect: "/search" },
  { path: "/session-overview", redirect: redirectLegacyTab("session-overview") },
  { path: "/transcript", redirect: redirectLegacyTab("transcript") },
  { path: "/chat-questions", redirect: redirectLegacyTab("chat-questions") },
  { path: "/analysis", redirect: redirectLegacyTab("analysis") },
  { path: "/smart-recap", redirect: redirectLegacyTab("smart-recap") },
  { path: "/content-repurposing", redirect: redirectLegacyTab("content-repurposing") },

  // ── Untouched ───────────────────────────────────────────────────────────
  { path: "/home", component: HomeView },           // available but not linked
  { path: "/beta-info", component: BetaNoticeView },
  { path: "/auth/callback", component: AuthCallbackView },
];

export default createRouter({
  history: createWebHistory(),
  routes,
});
