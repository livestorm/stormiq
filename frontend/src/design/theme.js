// Light and dark mode for the v1 design.
//
// By default the page follows the system (`prefers-color-scheme`). The theme
// toggle saves an explicit choice, which wins over the system until it is
// cleared. The choice lives in localStorage as a convenience: storage that is
// missing or throws only means the choice isn't remembered.
//
// An explicit choice is written to `<html data-theme="light|dark">`; with no
// choice the attribute is absent and tokens.css follows the system. index.html
// sets the attribute before first paint from the same storage key, so the
// page doesn't flash the wrong theme.
//
// Framework-free and dependency-injected so it can be tested without a
// browser; src/design/useTheme.js wraps it for Vue.

export const THEME_STORAGE_KEY = "stormiq:theme";
export const THEME_LIGHT = "light";
export const THEME_DARK = "dark";
const DARK_QUERY = "(prefers-color-scheme: dark)";

function isTheme(value) {
  return value === THEME_LIGHT || value === THEME_DARK;
}

/** The saved explicit choice ("light" or "dark"), or null to follow the system. */
export function readStoredTheme(storage) {
  try {
    const value = storage?.getItem(THEME_STORAGE_KEY);
    return isTheme(value) ? value : null;
  } catch {
    return null;
  }
}

/** Save an explicit choice, or forget it with null. Returns false when storage failed. */
export function writeStoredTheme(storage, choice) {
  try {
    if (!storage) return false;
    if (isTheme(choice)) storage.setItem(THEME_STORAGE_KEY, choice);
    else storage.removeItem(THEME_STORAGE_KEY);
    return true;
  } catch {
    return false;
  }
}

/** The theme on screen: the explicit choice if there is one, otherwise the system's. */
export function resolveTheme(choice, systemPrefersDark) {
  if (isTheme(choice)) return choice;
  return systemPrefersDark ? THEME_DARK : THEME_LIGHT;
}

/**
 * Create the theme state for one document.
 *
 * @param {object} options
 * @param {Storage|null} [options.storage]  where the explicit choice is saved
 * @param {Function|null} [options.matchMedia]  window.matchMedia, to read and follow the system
 * @param {Element|null} [options.root]  the element that carries data-theme
 */
export function createThemeController({ storage = null, matchMedia = null, root = null } = {}) {
  let choice = readStoredTheme(storage);
  let query = null;
  try {
    query = typeof matchMedia === "function" ? matchMedia(DARK_QUERY) : null;
  } catch {
    query = null;
  }
  let systemDark = Boolean(query?.matches);
  const listeners = new Set();

  function snapshot() {
    return { choice, theme: resolveTheme(choice, systemDark), systemDark };
  }

  function applyToRoot() {
    if (!root) return;
    if (isTheme(choice)) root.setAttribute("data-theme", choice);
    else root.removeAttribute("data-theme");
  }

  function notify() {
    const current = snapshot();
    for (const listener of listeners) listener(current);
  }

  function onSystemChange(event) {
    systemDark = Boolean(event?.matches);
    notify();
  }

  if (query) {
    if (typeof query.addEventListener === "function") query.addEventListener("change", onSystemChange);
    else if (typeof query.addListener === "function") query.addListener(onSystemChange);
  }
  applyToRoot();

  return {
    get choice() {
      return choice;
    },
    get theme() {
      return resolveTheme(choice, systemDark);
    },
    get systemDark() {
      return systemDark;
    },
    /** "light" or "dark" to choose explicitly; null to follow the system again. */
    setChoice(next) {
      choice = isTheme(next) ? next : null;
      writeStoredTheme(storage, choice);
      applyToRoot();
      notify();
      return snapshot();
    },
    /** Switch to the other theme of the one on screen, as an explicit choice. */
    toggle() {
      return this.setChoice(this.theme === THEME_DARK ? THEME_LIGHT : THEME_DARK);
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    destroy() {
      listeners.clear();
      if (!query) return;
      if (typeof query.removeEventListener === "function") query.removeEventListener("change", onSystemChange);
      else if (typeof query.removeListener === "function") query.removeListener(onSystemChange);
    },
  };
}
