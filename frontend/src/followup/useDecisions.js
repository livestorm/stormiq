// The decisions' data: one session's `GET /decisions?language=` (the saved
// list, with the speakers named as saved on the Transcript tab now). Nothing
// here calls a model.
//
// `createDecisions()` returns a small reactive model the component owns:
//   open(sessionId, language, version)  loads the list; again when any of the
//                                       three changed (`version`: the
//                                       workspace's text, which changes when
//                                       a job saves a new list)
//   reload()                            loads it again (after "Try again")
//   close()                             cancels everything (on unmount)
//
// Scope rules, as in the workspace store (frontend/CLAUDE.md): every request
// carries the open list's AbortController signal; opening another one aborts
// the old one and drops late results.

import { markRaw, reactive } from "vue";
import { api as defaultApi } from "../api";

export function createDecisions({ api = defaultApi } = {}) {
  const state = reactive({ sessionId: "", language: "English", loading: false, error: "", body: null });
  let scope = null;

  function isCurrent(own) {
    return own !== null && own === scope && !own.controller.signal.aborted;
  }

  function close() {
    if (scope) scope.controller.abort();
    scope = null;
  }

  async function load(own) {
    state.loading = true;
    try {
      const body = await api.getSessionDecisions(own.sessionId, own.language, { signal: own.controller.signal });
      if (!isCurrent(own)) return;
      state.body = body && typeof body === "object" ? markRaw(body) : null;
      state.error = "";
    } catch (error) {
      if (!isCurrent(own) || error?.name === "AbortError") return;
      state.error = error?.message || "The decisions could not be loaded.";
    } finally {
      if (isCurrent(own)) state.loading = false;
    }
  }

  function open(sessionId, language = "English", version = "") {
    const id = String(sessionId || "").trim();
    const lang = language === "French" ? "French" : "English";
    if (scope && scope.sessionId === id && scope.language === lang && scope.version === version) return;
    const sameList = scope && scope.sessionId === id && scope.language === lang;
    close();
    const own = { sessionId: id, language: lang, version, controller: new AbortController() };
    scope = own;
    // A new version of the same list keeps the cards on screen while it loads.
    Object.assign(state, { sessionId: id, language: lang, loading: false, error: "", body: sameList ? state.body : null });
    if (id) load(own);
  }

  function reload() {
    if (scope) load(scope);
  }

  return { state, open, reload, close };
}
