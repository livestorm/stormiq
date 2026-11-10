// Where the OAuth return page (`/auth/callback`) goes next. The server
// redirects there with `auth=success` (and `next`, the page the user
// connected from, when it is a path on this site) or `auth=error`.

export const CALLBACK_PATH = "/auth/callback";
const SIGNED_IN_HOME = "/sessions";

/** `value` when it is a path on this site (one leading "/", no backslash
 * or control character, not the OAuth pages or the API), else "". The
 * server checks the same before sending it; this is the second check. */
export function safeNextPath(value) {
  const path = String(value ?? "").trim();
  if (!path.startsWith("/") || path.startsWith("//")) return "";
  // eslint-disable-next-line no-control-regex
  if (/[\\\u0000-\u001f\u007f]/.test(path)) return "";
  if (path.length > 512 || path.startsWith("/auth/") || path.startsWith("/api/")) return "";
  return path;
}

/** The page to open after the OAuth return page, from its query string:
 * Sessions after a sign-in (or the page the user connected from), the home
 * page (the landing page) after a failed one. */
export function afterCallback(search) {
  const params = new URLSearchParams(search || "");
  if (params.get("auth") !== "success") return "/";
  const next = safeNextPath(params.get("next"));
  return next && next !== "/" ? next : SIGNED_IN_HOME;
}
