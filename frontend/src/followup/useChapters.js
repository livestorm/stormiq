// The chapters' data: one session's `GET /record/chapters` (the chapters the
// session analysis found, and their show notes). Nothing here calls a model.
//
// `createChapters()` returns a small reactive model the view owns (one per
// view, not a global store):
//   open(sessionId)   loads the chapters
//   reload()          loads them again (after "Try again")
//   close()           cancels everything (the view calls it on unmount)
//
// Scope rules, as in the workspace store (frontend/CLAUDE.md): every request
// carries the open session's AbortController signal; opening another session
// aborts the old one and drops late results.

import { markRaw, reactive } from "vue";
import { api as defaultApi } from "../api";

export function createChapters({ api = defaultApi } = {}) {
  const state = reactive({ sessionId: "", loading: false, error: "", body: null });
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
      const body = await api.getSessionChapters(own.sessionId, { signal: own.controller.signal });
      if (!isCurrent(own)) return;
      state.body = body && typeof body === "object" ? markRaw(body) : null;
      state.error = "";
    } catch (error) {
      if (!isCurrent(own) || error?.name === "AbortError") return;
      state.error = error?.message || "The chapters could not be loaded.";
    } finally {
      if (isCurrent(own)) state.loading = false;
    }
  }

  function open(sessionId) {
    const id = String(sessionId || "").trim();
    if (scope && scope.sessionId === id) return;
    close();
    const own = { sessionId: id, controller: new AbortController() };
    scope = own;
    Object.assign(state, { sessionId: id, loading: false, error: "", body: null });
    if (id) load(own);
  }

  function reload() {
    if (scope) load(scope);
  }

  return { state, open, reload, close };
}
