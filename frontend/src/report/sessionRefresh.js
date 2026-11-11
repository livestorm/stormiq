// "Fetch again from Livestorm", shared by the session's pages: the session,
// its chat and its questions read again (`POST /fetch-base` with
// `forceRefresh`: no transcript, nothing paid).
//
// It belongs to no page: leaving the page that started it never cancels it.
// When it ends, `refreshes[id].version` goes up, and every open page of that
// session reloads what it shows (the Report its record, the Transcript its
// lines); the Follow-up's copy of the session in the workspace store is
// dropped, so it reads the session again when it opens.

import { reactive } from "vue";
import { api as defaultApi } from "../api";
import { forgetSession } from "../store/workspace";

/** Per session id: `{status: idle | running | done | error, error, version}`. */
export const refreshes = reactive({});

export function refreshOf(sessionId) {
  return refreshes[sessionId] || { status: "idle", error: "", version: 0 };
}

/** Fetches the session again; resolves true when it was saved. A second call
 * while one runs joins it. */
export async function refetchSession(sessionId, { api = defaultApi } = {}) {
  const id = String(sessionId || "").trim();
  if (!id) return false;
  const current = refreshOf(id);
  if (current.status === "running") return current.promise;
  const run = (async () => {
    try {
      await api.fetchSessionBase(id, { apiKey: "", forceRefresh: true });
      forgetSession(id);
      refreshes[id] = { status: "done", error: "", version: current.version + 1 };
      return true;
    } catch (error) {
      refreshes[id] = { status: "error", error: error?.message || "The session could not be fetched again.", version: current.version };
      return false;
    }
  })();
  refreshes[id] = { status: "running", error: "", version: current.version, promise: run };
  return run;
}
