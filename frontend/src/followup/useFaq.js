// The editable FAQ's data: one session's `GET /record/faq`, the user's edits
// to a copy of its entries, and `PUT /record/faq` to save them whole.
// Nothing here calls a model.
//
// `createFaq()` returns a small reactive model the view owns:
//   open(sessionId)   loads the FAQ (the saved entries, or the desk's draft)
//   reload()          loads it again; unsaved edits are kept
//   state.entries     the entries on screen, edited in place by the editor
//   dirty             true while they differ from what was loaded or saved
//   setAnswer(i, a)   a new answer for entry i (its source follows: faq.js)
//   add() / move(i, d) / remove(i)
//   pending           the desk updates "Add them" would merge
//   addUpdates()      merges them (an entry with an answer is never changed)
//   save()            saves the entries; true when saved
//   discard()         back to what was loaded or saved
//   close()           cancels everything (the view calls it on unmount)
//
// Scope rules, as in the workspace store (frontend/CLAUDE.md): every request
// carries the open session's AbortController signal; opening another session
// aborts the old one and drops late results, late saves included.

import { computed, markRaw, reactive } from "vue";
import { api as defaultApi } from "../api";
import {
  entryProblems,
  isDirty,
  mergeUpdates,
  moveEntry,
  newEntry,
  pendingUpdates,
  removeEntry,
  withAnswer,
} from "./faq";

function isAbort(error) {
  return error?.name === "AbortError";
}

function copyEntries(entries) {
  return (entries || []).map((entry) => ({ ...entry, deskIds: [...(entry.deskIds || [])] }));
}

function blank() {
  return { sessionId: "", loading: false, error: "", body: null, entries: [], baseline: [], saving: false, saveError: "" };
}

export function createFaq({ api = defaultApi } = {}) {
  const state = reactive(blank());
  let scope = null;

  function isCurrent(own) {
    return own !== null && own === scope && !own.controller.signal.aborted;
  }

  const dirty = computed(() => Boolean(state.body) && isDirty(state.baseline, state.entries));
  const pending = computed(() => pendingUpdates(state.entries, state.body?.deskUpdates || []));
  const problems = computed(() => entryProblems(state.entries));

  // `keepEdits`: a reload while the user has unsaved edits takes the desk's
  // updates and leaves the entries on screen alone.
  function setBody(body, { keepEdits = false } = {}) {
    state.body = body && typeof body === "object" ? markRaw(body) : null;
    // Two copies: the editor changes `entries` in place, never the baseline.
    if (!keepEdits) state.entries = copyEntries(state.body?.entries);
    state.baseline = copyEntries(state.body?.entries);
  }

  function close() {
    if (scope) scope.controller.abort();
    scope = null;
  }

  async function load(own) {
    state.loading = true;
    try {
      const keepEdits = dirty.value;
      const body = await api.getSessionFaq(own.sessionId, { signal: own.controller.signal });
      if (!isCurrent(own)) return;
      setBody(body, { keepEdits: keepEdits && dirty.value });
      state.error = "";
    } catch (error) {
      if (!isCurrent(own) || isAbort(error)) return;
      state.error = error?.message || "The FAQ could not be loaded.";
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
    Object.assign(state, { ...blank(), sessionId: id });
    if (id) load(own);
  }

  function reload() {
    if (scope) load(scope);
  }

  function setAnswer(index, answer) {
    if (!state.entries[index]) return;
    state.entries[index] = withAnswer(state.entries[index], answer);
  }

  function add() {
    state.entries = [...state.entries, newEntry(state.entries)];
  }

  function move(index, delta) {
    state.entries = moveEntry(state.entries, index, delta);
  }

  function remove(index) {
    state.entries = removeEntry(state.entries, index);
  }

  function addUpdates() {
    state.entries = mergeUpdates(state.entries, state.body?.deskUpdates || []);
  }

  function discard() {
    state.entries = copyEntries(state.baseline);
    state.saveError = "";
  }

  async function save() {
    const own = scope;
    if (!own || state.saving) return false;
    if (problems.value.length) {
      state.saveError = problems.value[0].message;
      return false;
    }
    state.saving = true;
    state.saveError = "";
    try {
      const entries = copyEntries(state.entries);
      const body = await api.saveSessionFaq(own.sessionId, { entries }, { signal: own.controller.signal });
      if (!isCurrent(own)) return false;
      setBody(body);
      return true;
    } catch (error) {
      if (!isCurrent(own) || isAbort(error)) return false;
      state.saveError = error?.message || "The FAQ could not be saved.";
      return false;
    } finally {
      if (isCurrent(own)) state.saving = false;
    }
  }

  return { state, dirty, pending, problems, open, reload, setAnswer, add, move, remove, addUpdates, discard, save, close };
}
