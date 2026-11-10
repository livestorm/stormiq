// When the v1 shell shows the signed-out landing page (`views/LandingView.vue`)
// instead of the page the URL names. Pure, so it is tested on its own.

// Pages anyone may open: the beta notice, the design gallery and the OAuth
// return page.
export const PUBLIC_PATHS = new Set(["/beta-info", "/design", "/auth/callback"]);

/** True once the bootstrap has answered (`authReady`), nobody is signed in
 * (`authed` is false: no Livestorm user and no local API key), and the page
 * isn't a public one. Before the bootstrap answers, nothing is decided, so a
 * signed-in user never sees the landing page flash. */
export function showsLanding({ authReady, authed, path }) {
  return Boolean(authReady) && !authed && !PUBLIC_PATHS.has(path);
}
