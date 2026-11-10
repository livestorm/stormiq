import { createRouter, createWebHistory } from "vue-router";
import SingleAnalysisListView from "./views/SingleAnalysisListView.vue";
import { isV1 } from "./design/uiVersion";
import { useWorkspace } from "./store/workspace";

// The landing page (session list) ships in the entry bundle; every other
// view is a separate chunk loaded on first visit, so admin, transcript and
// analysis code doesn't slow down the first page load.
const SearchView = () => import("./views/SearchView.vue");
const CrossAnalysisView = () => import("./views/CrossAnalysisView.vue");
const SessionOverviewView = () => import("./views/SessionOverviewView.vue");
const TranscriptView = () => import("./views/TranscriptView.vue");
const ChatQuestionsView = () => import("./views/ChatQuestionsView.vue");
const AnalysisView = () => import("./views/AnalysisView.vue");
const SmartRecapView = () => import("./views/SmartRecapView.vue");
const ContentRepurposingView = () => import("./views/ContentRepurposingView.vue");
const AuthCallbackView = () => import("./views/AuthCallbackView.vue");
const BetaNoticeView = () => import("./views/BetaNoticeView.vue");
const AdminView = () => import("./views/AdminView.vue");
const DesignGalleryView = () => import("./views/DesignGalleryView.vue");
const SessionReportView = () => import("./views/SessionReportView.vue");
// The session list: the v1 index with the switch on, the classic card grid
// (in the entry bundle) otherwise.
const SessionsView = () => import("./views/SessionsView.vue");
const sessionListView = () => (isV1() ? SessionsView() : Promise.resolve(SingleAnalysisListView));

// Routing.
//
// Top-level navigation reflects the new product shape:
//   /search                                      — fetch sessions/events
//   /single-analysis                             — workspace card grid
//   /single-analysis/:sessionId/{tab}            — per-session analysis tabs
//   /cross-analysis                              — placeholder (cross-session analysis)
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
  // `v1Native` only matters in the v1 shell, where this route is the v1 index.
  { path: "/single-analysis", component: sessionListView, meta: { v1Native: true } },
  { path: "/cross-analysis", component: CrossAnalysisView },

  // ── Session detail (parameterised) ──────────────────────────────────────
  // The :sessionId is the URL contract for sharing. The store
  // synchronises state.workspace to whatever this segment carries.
  // With the v1 switch on, a session opens on the v1 Report.
  {
    path: "/single-analysis/:sessionId",
    redirect: (to) => ({ path: `/single-analysis/${to.params.sessionId}/${isV1() ? "report" : "session-overview"}` }),
  },
  // The v1 Report: v1 only; with the switch off it falls back to the overview.
  {
    path: "/single-analysis/:sessionId/report",
    component: SessionReportView,
    props: true,
    meta: { v1Native: true },
    beforeEnter: (to) => (isV1() ? true : { path: `/single-analysis/${to.params.sessionId}/session-overview` }),
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

  // ── Other pages ─────────────────────────────────────────────────────────
  { path: "/home", redirect: "/" },
  { path: "/beta-info", component: BetaNoticeView },
  { path: "/auth/callback", component: AuthCallbackView },
  { path: "/admin", component: AdminView },

  // ── v1 design ───────────────────────────────────────────────────────────
  // The component gallery, for reviewing the design foundation in light and
  // dark. Only reachable with the v1 switch on (?ui=v1); fictional content.
  {
    path: "/design",
    component: DesignGalleryView,
    meta: { v1Native: true },
    beforeEnter: () => (isV1() ? true : { path: "/" }),
  },
];

export default createRouter({
  history: createWebHistory(),
  routes,
});
