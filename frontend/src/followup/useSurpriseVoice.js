// Surprise me's voice: one session's `GET /surprise-voice` (the take's beats,
// cut by the server from the saved take, and which clips are made), and
// `POST /surprise-voice`, which queues the paid voice in the worker. The
// player calls `start()` only from a click on Play.
//
// `createSurpriseVoice()` returns a small reactive model the player owns:
//   open(sessionId)   reads the voice (and follows a job already running)
//   reload()          reads it again (a new take was written)
//   start()           the click on Play: queue the clips, then follow the job
//   close()           cancels everything (the player calls it on unmount)
//
// Scope rules, as in the workspace store (frontend/CLAUDE.md): every request
// carries the open session's AbortController signal; opening another session
// aborts the old one, stops its poll and drops late results. The poll
// survives blips (a network error, 408, 429, 5xx: retried with backoff,
// `MAX_FAILURES` in a row stop it); any other error stops it at once.

import { markRaw, reactive } from "vue";
import { api as defaultApi } from "../api";

export const POLL_MS = 1500;
export const MAX_POLL_MS = 20000;
export const MAX_FAILURES = 5;

const IN_FLIGHT = new Set(["pending", "running"]);

function retryable(error) {
  const status = error?.status;
  return !status || status === 408 || status === 429 || status >= 500;
}

export function createSurpriseVoice({ api = defaultApi } = {}) {
  // body: the server's answer, `{state, beats, ready, total, progress, error?}`.
  const state = reactive({ sessionId: "", loading: false, starting: false, error: "", body: null });
  let scope = null;

  function isCurrent(own) {
    return own !== null && own === scope && !own.controller.signal.aborted;
  }

  function stopPoll(own) {
    if (own?.timer) clearTimeout(own.timer);
    if (own) own.timer = null;
  }

  function close() {
    if (scope) {
      stopPoll(scope);
      scope.controller.abort();
    }
    scope = null;
  }

  function accept(own, body) {
    state.body = body && typeof body === "object" ? markRaw(body) : null;
    state.error = "";
    own.failures = 0;
    if (IN_FLIGHT.has(state.body?.state)) schedule(own);
    else stopPoll(own);
  }

  function schedule(own) {
    stopPoll(own);
    const delay = Math.min(POLL_MS * 2 ** own.failures, MAX_POLL_MS);
    own.timer = setTimeout(() => read(own, { poll: true }), delay);
  }

  async function read(own, { poll = false } = {}) {
    if (!isCurrent(own)) return;
    if (!poll) state.loading = true;
    try {
      const body = await api.getSurpriseVoice(own.sessionId, { signal: own.controller.signal });
      if (!isCurrent(own)) return;
      accept(own, body);
    } catch (error) {
      if (!isCurrent(own) || error?.name === "AbortError") return;
      if (poll && retryable(error) && own.failures + 1 < MAX_FAILURES) {
        own.failures += 1;
        schedule(own);
        return;
      }
      state.error = error?.message || "The voice could not be read.";
    } finally {
      if (isCurrent(own) && !poll) state.loading = false;
    }
  }

  function open(sessionId) {
    const id = String(sessionId || "").trim();
    if (scope && scope.sessionId === id) return;
    close();
    const own = { sessionId: id, controller: new AbortController(), timer: null, failures: 0 };
    scope = own;
    Object.assign(state, { sessionId: id, loading: false, starting: false, error: "", body: null });
    if (id) read(own);
  }

  function reload() {
    if (scope) read(scope);
  }

  /** The click on Play: queue the voice (paid). Resolves once the server
   * answered; the poll then follows the job until it is ready or fails. */
  async function start() {
    const own = scope;
    if (!own || state.starting) return;
    state.starting = true;
    state.error = "";
    try {
      const body = await api.startSurpriseVoice(own.sessionId, { signal: own.controller.signal });
      if (!isCurrent(own)) return;
      accept(own, body);
    } catch (error) {
      if (!isCurrent(own) || error?.name === "AbortError") return;
      state.error = error?.message || "The voice could not be started.";
    } finally {
      if (isCurrent(own)) state.starting = false;
    }
  }

  return { state, open, reload, start, close };
}
