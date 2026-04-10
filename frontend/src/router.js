import { createRouter, createWebHistory } from "vue-router";
import HomeView from "./views/HomeView.vue";
import EventsView from "./views/EventsView.vue";
import SessionOverviewView from "./views/SessionOverviewView.vue";
import TranscriptView from "./views/TranscriptView.vue";
import ChatQuestionsView from "./views/ChatQuestionsView.vue";
import AnalysisView from "./views/AnalysisView.vue";
import SmartRecapView from "./views/SmartRecapView.vue";
import ContentRepurposingView from "./views/ContentRepurposingView.vue";
import AuthCallbackView from "./views/AuthCallbackView.vue";
import BetaNoticeView from "./views/BetaNoticeView.vue";

const routes = [
  { path: "/", component: HomeView },
  { path: "/events", component: EventsView },
  { path: "/session-overview", component: SessionOverviewView },
  { path: "/transcript", component: TranscriptView },
  { path: "/chat-questions", component: ChatQuestionsView },
  { path: "/analysis", component: AnalysisView },
  { path: "/smart-recap", component: SmartRecapView },
  { path: "/content-repurposing", component: ContentRepurposingView },
  { path: "/beta-info", component: BetaNoticeView },
  { path: "/auth/callback", component: AuthCallbackView },
];

export default createRouter({
  history: createWebHistory(),
  routes,
});
