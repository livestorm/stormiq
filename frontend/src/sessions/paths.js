// Session URLs. The list is `/sessions` and a session's pages are
// `/sessions/:sessionId/{tab}`; until November 2026 they were under
// `/single-analysis`, and links shared from then still open (the router
// redirects them with `renamedSessionsPath`).

/** `/single-analysis/…` → `/sessions/…`, the rest of the URL (the session
 * id, the tab, the query and the hash) unchanged. Any other path is
 * returned as it is. */
export function renamedSessionsPath(fullPath) {
  return String(fullPath).replace(/^\/single-analysis(?=[/?#]|$)/, "/sessions");
}
