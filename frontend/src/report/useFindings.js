// The Report's reads that come on demand, beside the record: one session's
// `GET /record/findings` ("What stood out") and `GET /record/voices` ("Who
// spoke up") and `GET /record/moments` (the Pulse's key moments, told).
// Nothing here calls a model.
//
// `createFindings()`, `createMoments()` and `createVoices()` each return a small reactive model
// the view owns (one per view, not a global store), with the same scope
// rules as the chapters (`followup/useChapters.js`): every request carries
// the open session's AbortController signal; opening another session aborts
// the old one and drops late results.
//   open(sessionId)   loads it
//   reload()          loads it again (after a run of the analysis)
//   close()           cancels everything (the view calls it on unmount)

import { markRaw, reactive } from "vue";
import { api as defaultApi } from "../api";

function createRead(get, failure) {
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
      const body = await get(own.sessionId, { signal: own.controller.signal });
      if (!isCurrent(own)) return;
      state.body = body && typeof body === "object" ? markRaw(body) : null;
      state.error = "";
    } catch (error) {
      if (!isCurrent(own) || error?.name === "AbortError") return;
      state.error = error?.message || failure;
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

export function createFindings({ api = defaultApi } = {}) {
  return createRead((id, options) => api.getSessionFindings(id, options), "The findings could not be loaded.");
}

export function createMoments({ api = defaultApi } = {}) {
  return createRead((id, options) => api.getSessionMoments(id, options), "The key moments could not be loaded.");
}

export function createVoices({ api = defaultApi } = {}) {
  return createRead((id, options) => api.getSessionVoices(id, options), "The list of who wrote could not be loaded.");
}
