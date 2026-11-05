// The v1 design switch.
//
// The redesign ships behind an opt-in switch, off by default, so production
// users keep the classic app until they ask for the new one:
//   ?ui=v1       turns the v1 design on and remembers it in this browser
//   ?ui=classic  turns it off and forgets it
// Anything else keeps whatever this browser remembered (classic if nothing).
//
// The choice is a convenience saved in localStorage, which can be missing or
// throw (private windows, blocked site data): every access is guarded and the
// switch still works for the current page load.
//
// index.html runs a tiny copy of this logic before first paint (same storage
// key and values) so the page doesn't flash the classic layout. Keep the two
// in sync.

export const UI_STORAGE_KEY = "stormiq:ui";
export const UI_V1 = "v1";
export const UI_CLASSIC = "classic";
const UI_QUERY_PARAM = "ui";

let currentVersion = UI_CLASSIC;

/** The browser's localStorage, or null when it's missing or reading it throws. */
export function safeStorage(win = globalThis.window) {
  try {
    return win?.localStorage ?? null;
  } catch {
    return null;
  }
}

/** "v1", "classic", or null when the query string doesn't ask for either. */
export function parseUiParam(search) {
  if (!search) return null;
  let value = null;
  try {
    value = new URLSearchParams(search).get(UI_QUERY_PARAM);
  } catch {
    return null;
  }
  const normalised = String(value ?? "").trim().toLowerCase();
  return normalised === UI_V1 || normalised === UI_CLASSIC ? normalised : null;
}

/** The version remembered in this browser: "v1", or "classic" by default. */
export function readStoredUiVersion(storage) {
  try {
    return storage?.getItem(UI_STORAGE_KEY) === UI_V1 ? UI_V1 : UI_CLASSIC;
  } catch {
    return UI_CLASSIC;
  }
}

/**
 * Decide which design this page load uses, saving an explicit `?ui=` choice.
 * Returns "v1" or "classic". Never throws.
 */
export function resolveUiVersion({ search = "", storage = null } = {}) {
  const requested = parseUiParam(search);
  if (requested === UI_V1) {
    try {
      storage?.setItem(UI_STORAGE_KEY, UI_V1);
    } catch {
      // Not saved: the switch still applies to this page load.
    }
    return UI_V1;
  }
  if (requested === UI_CLASSIC) {
    try {
      storage?.removeItem(UI_STORAGE_KEY);
    } catch {
      // Nothing to forget, or storage is unavailable.
    }
    return UI_CLASSIC;
  }
  return readStoredUiVersion(storage);
}

/** Mark the document so v1-only CSS (`:root[data-ui="v1"]`) applies. */
export function applyUiVersion(version, root = globalThis.document?.documentElement) {
  if (!root) return;
  if (version === UI_V1) root.setAttribute("data-ui", UI_V1);
  else root.removeAttribute("data-ui");
}

/** Resolve, remember and apply the version for this page load (called once, in main.js). */
export function initUiVersion(win = globalThis.window) {
  currentVersion = resolveUiVersion({ search: win?.location?.search ?? "", storage: safeStorage(win) });
  applyUiVersion(currentVersion, win?.document?.documentElement);
  return currentVersion;
}

/** The version this page load uses. Changing it takes a reload with `?ui=`. */
export function currentUiVersion() {
  return currentVersion;
}

export function isV1() {
  return currentVersion === UI_V1;
}
