// The Report's findings: one session's `GET /record/findings`. Nothing here
// calls a model.
//
// `createFindings()` returns a small reactive model the view owns (one per
// view, not a global store), with the same scope rules as the chapters
// (`followup/useChapters.js`): every request carries the open session's
// AbortController signal; opening another session aborts the old one and
// drops late results.
//   open(sessionId)   loads the findings
//   reload()          loads them again (after a run of the analysis)
//   close()           cancels everything (the view calls it on unmount)

import { markRaw, reactive } from "vue";
import { api as defaultApi } from "../api";

export function createFindings({ api = defaultApi } = {}) {
  const state = reactive({ sessionId: "", loading: false, error: "", body: null });
  let scope = null;

  const isCurrent = (own) => own !== null && own === scope && !own.controller.signal.aborted;

  function close() {
    if (scope) scope.controller.abort();
    scope = null;
  }

  async function load(own) {
    state.loading = true;
    try {
      const body = await api.getSessionFindings(own.sessionId, { signal: own.controller.signal });
      if (!isCurrent(own)) return;
      state.body = body && typeof body === "object" ? markRaw(body) : null;
      state.error = "";
    } catch (error) {
      if (!isCurrent(own) || error?.name === "AbortError") return;
      state.error = error?.message || "The findings could not be loaded.";
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
