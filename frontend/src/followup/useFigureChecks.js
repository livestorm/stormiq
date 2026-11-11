// Before you share's data: one session's `GET /record/figures`, and the
// host's answers (`PUT /record/figures`). Nothing here calls a model.
//
// `createFigureChecks()` returns a small reactive model the view owns:
//   open(sessionId)        loads the checks
//   reload()               loads them again
//   answer(key, choice)    saves one answer (null reopens the check), then
//                          shows what the server sends back; true when saved
//   close()                cancels everything (the view calls it on unmount)
//
// Scope rules, as in the workspace store (frontend/CLAUDE.md): every request
// carries the open session's AbortController signal; opening another session
// aborts the old one and drops late results.

import { markRaw, reactive } from "vue";
import { api as defaultApi } from "../api";

function isAbort(error) {
  return error?.name === "AbortError";
}

export function createFigureChecks({ api = defaultApi } = {}) {
  const state = reactive({ sessionId: "", loading: false, error: "", body: null, saving: "", saveError: { key: "", message: "" } });
  let scope = null;

  function isCurrent(own) {
    return own !== null && own === scope && !own.controller.signal.aborted;
  }

  function setBody(body) {
    state.body = body && typeof body === "object" ? markRaw(body) : null;
  }

  function close() {
    if (scope) scope.controller.abort();
    scope = null;
  }

  async function load(own) {
    state.loading = true;
    try {
      const body = await api.getFigureChecks(own.sessionId, { signal: own.controller.signal });
      if (!isCurrent(own)) return;
      setBody(body);
      state.error = "";
    } catch (error) {
      if (!isCurrent(own) || isAbort(error)) return;
      state.error = error?.message || "The figures to check could not be loaded.";
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
    Object.assign(state, { sessionId: id, loading: false, error: "", body: null, saving: "", saveError: { key: "", message: "" } });
    if (id) load(own);
  }

  function reload() {
    if (scope) load(scope);
  }

  async function answer(key, choice) {
    const own = scope;
    if (!own || state.saving) return false;
    state.saving = key;
    state.saveError = { key: "", message: "" };
    try {
      const body = await api.saveFigureCheck(own.sessionId, { key, choice }, { signal: own.controller.signal });
      if (!isCurrent(own)) return false;
      setBody(body);
      return true;
    } catch (error) {
      if (!isCurrent(own) || isAbort(error)) return false;
      state.saveError = { key, message: error?.message || "It could not be saved." };
      return false;
    } finally {
      if (isCurrent(own)) state.saving = "";
    }
  }

  return { state, open, reload, answer, close };
}
