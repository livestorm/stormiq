// Follow-up's "Earlier versions": the saved versions of the output on screen
// (`GET /output-versions`). Read-only; nothing here calls a model.
//
// `createOutputVersions()` returns a small reactive model the view owns:
//   open(sessionId, query)   loads one output's versions (`versionsQuery` in
//                            followup/outputs.js; null shows none)
//   reload()                 loads them again (after a new version is saved)
//   close()                  cancels everything (the view calls it on unmount)
//
// Scope rules, as in the workspace store (frontend/CLAUDE.md): every request
// carries its own AbortController signal; opening another output or session
// aborts the old request and drops late results.

import { markRaw, reactive } from "vue";
import { api as defaultApi } from "../api";

function isAbort(error) {
  return error?.name === "AbortError";
}

function keyOf(sessionId, query) {
  return query ? `${sessionId}|${JSON.stringify(query)}` : "";
}

export function createOutputVersions({ api = defaultApi } = {}) {
  const state = reactive({ key: "", loading: false, error: "", body: null });
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
      const body = await api.getOutputVersions(own.sessionId, own.query, { signal: own.controller.signal });
      if (!isCurrent(own)) return;
      state.body = body && typeof body === "object" ? markRaw(body) : null;
      state.error = "";
    } catch (error) {
      if (!isCurrent(own) || isAbort(error)) return;
      state.error = error?.message || "The earlier versions could not be loaded.";
    } finally {
      if (isCurrent(own)) state.loading = false;
    }
  }

  function open(sessionId, query) {
    const id = String(sessionId || "").trim();
    const key = id ? keyOf(id, query) : "";
    if (scope && state.key === key) return;
    close();
    Object.assign(state, { key, loading: false, error: "", body: null });
    if (!key) return;
    const own = { sessionId: id, query, controller: new AbortController() };
    scope = own;
    load(own);
  }

  function reload() {
    if (!scope) return;
    const own = { ...scope, controller: new AbortController() };
    scope.controller.abort();
    scope = own;
    load(own);
  }

  return { state, open, reload, close };
}
