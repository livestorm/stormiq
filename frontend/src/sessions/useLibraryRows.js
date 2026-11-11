// The Sessions library's rows: one `GET /record/library` per session, asked
// only for the rows that come on screen (`want`), a few at a time, so a long
// list never asks for every session at once. Read-only; no model call.
//
// `createLibraryRows()` returns a small reactive model the view owns:
//   want(sessionId)   asks for that session's row, once
//   close()           cancels everything (the view calls it on unmount)
//
// `state.rows[id]` is `{loading, error, body}`. A failed row stays failed
// for this visit: the list still shows Livestorm's counts.

import { markRaw, reactive } from "vue";
import { api as defaultApi } from "../api";

export const CONCURRENCY = 3;

export function createLibraryRows({ api = defaultApi, concurrency = CONCURRENCY } = {}) {
  const state = reactive({ rows: {} });
  const queue = [];
  let controller = new AbortController();
  let running = 0;

  async function run(id, signal) {
    running += 1;
    try {
      const body = await api.getSessionLibraryRow(id, { signal });
      if (signal.aborted) return;
      state.rows[id] = { loading: false, error: "", body: body && typeof body === "object" ? markRaw(body) : null };
    } catch (error) {
      if (signal.aborted || error?.name === "AbortError") return;
      state.rows[id] = { loading: false, error: error?.message || "This session's summary could not be loaded.", body: null };
    } finally {
      // Only this generation's count: `close()` started a new one.
      if (signal === controller.signal) {
        running -= 1;
        next();
      }
    }
  }

  function next() {
    while (running < concurrency && queue.length) run(queue.shift(), controller.signal);
  }

  function want(sessionId) {
    const id = String(sessionId || "").trim();
    if (!id || state.rows[id]) return;
    state.rows[id] = { loading: true, error: "", body: null };
    queue.push(id);
    next();
  }

  function close() {
    controller.abort();
    controller = new AbortController();
    queue.length = 0;
    running = 0;
  }

  return { state, want, close };
}
