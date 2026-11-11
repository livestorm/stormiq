// The question desk's data: one session's `GET /record/questions`, and the
// host's marks (`PUT /record/questions`).
//
// `createQuestionDesk()` returns a small reactive model the view owns (one
// per view, not a global store):
//   open(sessionId)              loads the desk
//   reload()                     loads it again (after "Try again")
//   mark(questionId, {answered, reply})
//                                saves one question, then shows the desk the
//                                server sends back; resolves true when saved
//   decide(questionId, "confirm" | "reject")
//                                the host's decision on an answer located in
//                                the talk; same contract as mark()
//   close()                      cancels everything (the view calls it on unmount)
//
// Scope rules, as in the workspace store (frontend/CLAUDE.md): every request
// carries the open session's AbortController signal; opening another session
// aborts the old one and drops late results. Nothing here calls a model.

import { markRaw, reactive } from "vue";
import { api as defaultApi } from "../api";

function isAbort(error) {
  return error?.name === "AbortError";
}

export function createQuestionDesk({ api = defaultApi } = {}) {
  const state = reactive({
    sessionId: "",
    loading: false,
    error: "",
    body: null, // the route's whole response
    saving: "", // the id being saved, or ""
    saveError: { id: "", message: "" },
  });
  let scope = null;

  function isCurrent(own) {
    return own !== null && own === scope && !own.controller.signal.aborted;
  }

  function setBody(body) {
    // Raw: replaced, never mutated (as the store does with the workspace).
    state.body = body && typeof body === "object" ? markRaw(body) : null;
  }

  function close() {
    if (scope) scope.controller.abort();
    scope = null;
  }

  async function load(own) {
    state.loading = true;
    try {
      const body = await api.getSessionQuestionDesk(own.sessionId, { signal: own.controller.signal });
      if (!isCurrent(own)) return;
      setBody(body);
      state.error = "";
    } catch (error) {
      if (!isCurrent(own) || isAbort(error)) return;
      state.error = error?.message || "The questions could not be loaded.";
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
    Object.assign(state, { sessionId: id, loading: false, error: "", body: null, saving: "", saveError: { id: "", message: "" } });
    if (id) load(own);
  }

  function reload() {
    if (scope) load(scope);
  }

  // One write for one question: the server answers with the whole desk.
  async function save(questionId, send) {
    const own = scope;
    if (!own || state.saving) return false;
    state.saving = questionId;
    state.saveError = { id: "", message: "" };
    try {
      const body = await send(own);
      if (!isCurrent(own)) return false;
      setBody(body);
      return true;
    } catch (error) {
      if (!isCurrent(own) || isAbort(error)) return false;
      state.saveError = { id: questionId, message: error?.message || "It could not be saved." };
      return false;
    } finally {
      if (isCurrent(own)) state.saving = "";
    }
  }

  function mark(questionId, { answered, reply = "" } = {}) {
    return save(questionId, (own) =>
      api.saveSessionQuestion(own.sessionId, { questionId, answered, reply }, { signal: own.controller.signal }),
    );
  }

  function decide(questionId, decision) {
    return save(questionId, (own) =>
      api.decideLocatedAnswer(own.sessionId, { questionId, decision }, { signal: own.controller.signal }),
    );
  }

  return { state, open, reload, mark, decide, close };
}
