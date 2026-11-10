import { createRouter, createWebHistory } from "vue-router";
import SessionsView from "./views/SessionsView.vue";
import { renamedSessionsPath } from "./sessions/paths";
import { useWorkspace } from "./store/workspace";

// The session list ships in the entry bundle; every other
// view is a separate chunk loaded on first visit, so admin, transcript and
// analysis code doesn't slow down the first page load.
const AddSessionView = () => import("./views/AddSessionView.vue");
const CrossAnalysisView = () => import("./views/CrossAnalysisView.vue");
const SessionOverviewView = () => import("./views/SessionOverviewView.vue");
const SessionTranscriptView = () => import("./views/SessionTranscriptView.vue");
const ChatQuestionsView = () => import("./views/ChatQuestionsView.vue");
const AnalysisView = () => import("./views/AnalysisView.vue");
const SmartRecapView = () => import("./views/SmartRecapView.vue");
const ContentRepurposingView = () => import("./views/ContentRepurposingView.vue");
const AuthCallbackView = () => import("./views/AuthCallbackView.vue");
const BetaNoticeView = () => import("./views/BetaNoticeView.vue");
const AdminView = () => import("./views/AdminView.vue");
const DesignGalleryView = () => import("./views/DesignGalleryView.vue");
const SessionReportView = () => import("./views/SessionReportView.vue");
const HomeView = () => import("./views/HomeView.vue");

// Routing.
//
//   /                                — home: the landing page signed out, Sessions signed in
//   /sessions                        — every session the organization has fetched
//   /sessions/:sessionId/{tab}       — one session's pages (it opens on its Report)
//   /sessions/add                    — add a session (was /search)
//   /cross-analysis                  — placeholder (cross-session analysis)
//
// A session's URL is a sharing contract: teammates in the same Livestorm org
// paste /sessions/:sessionId/... and land on that page. Links from before
// the rename (/single-analysis/...) redirect to the same path under
// /sessions, query and hash included, so every link shared so far still
// opens. Older bookmarks (/transcript, /analysis…) redirect when the store
// has an active session, otherwise to the list.

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
    return sessionId ? { path: `/sessions/${sessionId}/${tab}` } : { path: "/sessions" };
  };
}

const routes = [
  // ── Top level ───────────────────────────────────────────────────────────
  // The site's home: signed out, the shell shows the landing page here;
  // signed in, HomeView moves on to Sessions.
  { path: "/", component: HomeView, meta: { v1Native: true } },
  { path: "/sessions/add", component: AddSessionView, meta: { v1Native: true } },
  { path: "/search", redirect: "/sessions/add" },
  { path: "/sessions", component: SessionsView, meta: { v1Native: true } },
  { path: "/cross-analysis", component: CrossAnalysisView },

  // ── Session detail (parameterised) ──────────────────────────────────────
  // The :sessionId is the URL contract for sharing. The store
  // synchronises state.workspace to whatever this segment carries.
  // A session opens on its Report.
  { path: "/sessions/:sessionId", redirect: (to) => ({ path: `/sessions/${encodeURIComponent(to.params.sessionId)}/report` }) },
  { path: "/sessions/:sessionId/report", component: SessionReportView, props: true, meta: { v1Native: true } },
  { path: "/sessions/:sessionId/session-overview", component: SessionOverviewView, props: true },
  { path: "/sessions/:sessionId/transcript", component: SessionTranscriptView, props: true, meta: { v1Native: true } },
  { path: "/sessions/:sessionId/chat-questions", component: ChatQuestionsView, props: true },
  { path: "/sessions/:sessionId/analysis", component: AnalysisView, props: true },
  { path: "/sessions/:sessionId/smart-recap", component: SmartRecapView, props: true },
  { path: "/sessions/:sessionId/content-repurposing", component: ContentRepurposingView, props: true },

  // ── Legacy redirects ────────────────────────────────────────────────────
  // Old bookmarks land on the new equivalent. When there's no active
  // session in the store (e.g. cold load), we fall through to the list.
  // The URL rename: every /single-analysis link, a session's pages included.
  { path: "/single-analysis", redirect: (to) => renamedSessionsPath(to.fullPath) },
  { path: "/single-analysis/:rest(.*)", redirect: (to) => renamedSessionsPath(to.fullPath) },
  { path: "/events", redirect: "/sessions/add" },
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

  // The component gallery, for reviewing the design system in light and
  // dark; fictional content.
  { path: "/design", component: DesignGalleryView, meta: { v1Native: true } },
];

export default createRouter({
  history: createWebHistory(),
  routes,
});
