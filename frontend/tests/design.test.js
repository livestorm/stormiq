import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  THEME_STORAGE_KEY,
  createThemeController,
  readStoredTheme,
  resolveTheme,
  writeStoredTheme,
} from "../src/design/theme.js";
import {
  UI_STORAGE_KEY,
  applyUiVersion,
  currentUiVersion,
  initUiVersion,
  isV1,
  parseUiParam,
  readStoredUiVersion,
  resolveUiVersion,
  safeStorage,
} from "../src/design/uiVersion.js";

function memoryStorage(initial = {}) {
  const data = new Map(Object.entries(initial));
  return {
    getItem: vi.fn((key) => (data.has(key) ? data.get(key) : null)),
    setItem: vi.fn((key, value) => data.set(key, String(value))),
    removeItem: vi.fn((key) => data.delete(key)),
    data,
  };
}

function throwingStorage() {
  const fail = () => {
    throw new Error("SecurityError: storage is disabled");
  };
  return { getItem: vi.fn(fail), setItem: vi.fn(fail), removeItem: vi.fn(fail) };
}

function fakeRoot() {
  const attributes = new Map();
  return {
    setAttribute: (name, value) => attributes.set(name, String(value)),
    removeAttribute: (name) => attributes.delete(name),
    getAttribute: (name) => (attributes.has(name) ? attributes.get(name) : null),
  };
}

function fakeMatchMedia(matches) {
  const listeners = new Set();
  const query = {
    matches,
    addEventListener: vi.fn((_type, listener) => listeners.add(listener)),
    removeEventListener: vi.fn((_type, listener) => listeners.delete(listener)),
  };
  const matchMedia = vi.fn(() => query);
  matchMedia.change = (next) => {
    query.matches = next;
    for (const listener of listeners) listener({ matches: next });
  };
  matchMedia.query = query;
  return matchMedia;
}

describe("uiVersion", () => {
  it("is classic by default", () => {
    expect(resolveUiVersion()).toBe("classic");
    expect(resolveUiVersion({ search: "", storage: memoryStorage() })).toBe("classic");
    expect(readStoredUiVersion(null)).toBe("classic");
  });

  it("turns v1 on with ?ui=v1 and remembers it", () => {
    const storage = memoryStorage();
    expect(resolveUiVersion({ search: "?ui=v1", storage })).toBe("v1");
    expect(storage.data.get(UI_STORAGE_KEY)).toBe("v1");
    // Next page load, no query: still v1.
    expect(resolveUiVersion({ search: "", storage })).toBe("v1");
    expect(resolveUiVersion({ search: "?tab=chat", storage })).toBe("v1");
  });

  it("turns v1 off with ?ui=classic and forgets it", () => {
    const storage = memoryStorage({ [UI_STORAGE_KEY]: "v1" });
    expect(resolveUiVersion({ search: "?ui=classic", storage })).toBe("classic");
    expect(storage.data.has(UI_STORAGE_KEY)).toBe(false);
    expect(resolveUiVersion({ search: "", storage })).toBe("classic");
  });

  it("is off before initUiVersion runs", async () => {
    vi.resetModules();
    const fresh = await import("../src/design/uiVersion.js");
    expect(fresh.currentUiVersion()).toBe("classic");
    expect(fresh.isV1()).toBe(false);
  });

  it("parses only the two known values, case-insensitively", () => {
    expect(parseUiParam("?ui=V1")).toBe("v1");
    expect(parseUiParam("?ui=%20v1%20")).toBe("v1");
    expect(parseUiParam("?ui=%20Classic")).toBe("classic");
    expect(parseUiParam("?a=1&ui=classic")).toBe("classic");
    expect(parseUiParam("?ui=v2")).toBeNull();
    expect(parseUiParam("?ui=")).toBeNull();
    expect(parseUiParam("")).toBeNull();
    expect(parseUiParam(undefined)).toBeNull();
  });

  it("ignores unknown stored values", () => {
    expect(readStoredUiVersion(memoryStorage({ [UI_STORAGE_KEY]: "v9" }))).toBe("classic");
  });

  it("still applies ?ui= for this page load when storage throws", () => {
    const storage = throwingStorage();
    expect(resolveUiVersion({ search: "?ui=v1", storage })).toBe("v1");
    expect(resolveUiVersion({ search: "?ui=classic", storage })).toBe("classic");
    expect(resolveUiVersion({ search: "", storage })).toBe("classic");
  });

  it("treats a localStorage getter that throws as no storage", () => {
    const win = {};
    Object.defineProperty(win, "localStorage", {
      get() {
        throw new Error("SecurityError");
      },
    });
    expect(safeStorage(win)).toBeNull();
    expect(safeStorage(undefined)).toBeNull();
  });

  it("marks the document and exposes the version for the page load", () => {
    const root = fakeRoot();
    const storage = memoryStorage();
    const win = { location: { search: "?ui=v1" }, localStorage: storage, document: { documentElement: root } };
    expect(initUiVersion(win)).toBe("v1");
    expect(root.getAttribute("data-ui")).toBe("v1");
    expect(currentUiVersion()).toBe("v1");
    expect(isV1()).toBe(true);

    win.location.search = "?ui=classic";
    expect(initUiVersion(win)).toBe("classic");
    expect(root.getAttribute("data-ui")).toBeNull();
    expect(isV1()).toBe(false);
  });

  it("initUiVersion saves ?ui=v1 for later page loads and forgets it on ?ui=classic", () => {
    const root = fakeRoot();
    const storage = memoryStorage();
    const win = { location: { search: "?ui=v1" }, localStorage: storage, document: { documentElement: root } };
    initUiVersion(win);
    expect(storage.data.get(UI_STORAGE_KEY)).toBe("v1");

    // A later page load without the query keeps v1.
    win.location.search = "";
    expect(initUiVersion(win)).toBe("v1");
    expect(root.getAttribute("data-ui")).toBe("v1");

    win.location.search = "?ui=classic";
    initUiVersion(win);
    expect(storage.data.has(UI_STORAGE_KEY)).toBe(false);

    win.location.search = "";
    expect(initUiVersion(win)).toBe("classic");
    expect(root.getAttribute("data-ui")).toBeNull();
  });

  it("initUiVersion with no query and nothing saved leaves the document classic", () => {
    const root = fakeRoot();
    const win = { location: { search: "" }, localStorage: memoryStorage(), document: { documentElement: root } };
    expect(initUiVersion(win)).toBe("classic");
    expect(root.getAttribute("data-ui")).toBeNull();
  });

  it("applyUiVersion tolerates a missing root", () => {
    expect(() => applyUiVersion("v1", null)).not.toThrow();
  });
});

describe("theme", () => {
  let root;

  beforeEach(() => {
    root = fakeRoot();
  });

  it("resolves an explicit choice over the system", () => {
    expect(resolveTheme(null, false)).toBe("light");
    expect(resolveTheme(null, true)).toBe("dark");
    expect(resolveTheme("light", true)).toBe("light");
    expect(resolveTheme("dark", false)).toBe("dark");
    expect(resolveTheme("sepia", true)).toBe("dark");
  });

  it("follows the system by default and sets no data-theme", () => {
    const controller = createThemeController({ storage: memoryStorage(), matchMedia: fakeMatchMedia(true), root });
    expect(controller.choice).toBeNull();
    expect(controller.theme).toBe("dark");
    expect(root.getAttribute("data-theme")).toBeNull();
  });

  it("is light when nothing is known about the system", () => {
    const controller = createThemeController({ storage: null, matchMedia: null, root });
    expect(controller.theme).toBe("light");
  });

  it("restores a saved choice and writes it on the root", () => {
    const storage = memoryStorage({ [THEME_STORAGE_KEY]: "dark" });
    const controller = createThemeController({ storage, matchMedia: fakeMatchMedia(false), root });
    expect(controller.choice).toBe("dark");
    expect(controller.theme).toBe("dark");
    expect(root.getAttribute("data-theme")).toBe("dark");
  });

  it("ignores an invalid saved value", () => {
    expect(readStoredTheme(memoryStorage({ [THEME_STORAGE_KEY]: "neon" }))).toBeNull();
  });

  it("toggle saves the opposite of what's on screen as an explicit choice", () => {
    const storage = memoryStorage();
    const controller = createThemeController({ storage, matchMedia: fakeMatchMedia(true), root });
    controller.toggle();
    expect(controller.choice).toBe("light");
    expect(controller.theme).toBe("light");
    expect(storage.data.get(THEME_STORAGE_KEY)).toBe("light");
    expect(root.getAttribute("data-theme")).toBe("light");

    controller.toggle();
    expect(controller.theme).toBe("dark");
    expect(storage.data.get(THEME_STORAGE_KEY)).toBe("dark");
  });

  it("setChoice(null) goes back to following the system", () => {
    const storage = memoryStorage({ [THEME_STORAGE_KEY]: "light" });
    const controller = createThemeController({ storage, matchMedia: fakeMatchMedia(true), root });
    controller.setChoice(null);
    expect(controller.choice).toBeNull();
    expect(controller.theme).toBe("dark");
    expect(storage.data.has(THEME_STORAGE_KEY)).toBe(false);
    expect(root.getAttribute("data-theme")).toBeNull();
  });

  it("an explicit choice wins over system changes; without one the theme follows them", () => {
    const matchMedia = fakeMatchMedia(false);
    const controller = createThemeController({ storage: memoryStorage(), matchMedia, root });
    const seen = [];
    controller.subscribe((current) => seen.push(current.theme));

    matchMedia.change(true);
    expect(controller.theme).toBe("dark");

    controller.setChoice("light");
    matchMedia.change(false);
    matchMedia.change(true);
    expect(controller.theme).toBe("light");
    expect(controller.systemDark).toBe(true);
    expect(seen).toEqual(["dark", "light", "light", "light"]);
  });

  it("works when storage throws: the choice applies but isn't remembered", () => {
    const storage = throwingStorage();
    const controller = createThemeController({ storage, matchMedia: fakeMatchMedia(false), root });
    expect(controller.choice).toBeNull();
    expect(() => controller.toggle()).not.toThrow();
    expect(controller.theme).toBe("dark");
    expect(root.getAttribute("data-theme")).toBe("dark");
    expect(writeStoredTheme(storage, "dark")).toBe(false);
    expect(writeStoredTheme(null, "dark")).toBe(false);
  });

  it("works when matchMedia throws", () => {
    const matchMedia = () => {
      throw new Error("not supported");
    };
    const controller = createThemeController({ storage: memoryStorage(), matchMedia, root });
    expect(controller.theme).toBe("light");
  });

  it("stops listening and notifying on destroy", () => {
    const matchMedia = fakeMatchMedia(false);
    const controller = createThemeController({ storage: null, matchMedia, root });
    const listener = vi.fn();
    controller.subscribe(listener);
    const added = matchMedia.query.addEventListener.mock.calls[0][1];

    controller.destroy();
    expect(matchMedia.query.removeEventListener).toHaveBeenCalledWith("change", added);
    matchMedia.change(true);
    expect(listener).not.toHaveBeenCalled();
  });

  it("an unsubscribed listener is no longer called", () => {
    const matchMedia = fakeMatchMedia(false);
    const controller = createThemeController({ storage: null, matchMedia, root });
    const kept = vi.fn();
    const dropped = vi.fn();
    controller.subscribe(kept);
    const unsubscribe = controller.subscribe(dropped);
    unsubscribe();
    controller.toggle();
    expect(kept).toHaveBeenCalledOnce();
    expect(dropped).not.toHaveBeenCalled();
  });

  it("treats an unknown choice as following the system", () => {
    const storage = memoryStorage({ [THEME_STORAGE_KEY]: "dark" });
    const controller = createThemeController({ storage, matchMedia: fakeMatchMedia(false), root });
    controller.setChoice("sepia");
    expect(controller.choice).toBeNull();
    expect(controller.theme).toBe("light");
    expect(storage.data.has(THEME_STORAGE_KEY)).toBe(false);
    expect(root.getAttribute("data-theme")).toBeNull();
  });

  it("supports the older addListener API and stops listening on destroy", () => {
    const listeners = new Set();
    const query = {
      matches: false,
      addListener: vi.fn((listener) => listeners.add(listener)),
      removeListener: vi.fn((listener) => listeners.delete(listener)),
    };
    const controller = createThemeController({ storage: null, matchMedia: () => query, root });
    expect(query.addListener).toHaveBeenCalledOnce();
    for (const listener of listeners) listener({ matches: true });
    expect(controller.theme).toBe("dark");
    controller.destroy();
    expect(query.removeListener).toHaveBeenCalledOnce();
    expect(listeners.size).toBe(0);
  });
});

// The inline script in index.html repeats the switch and theme logic so the
// first paint is right. Run it against fake globals and check it agrees with
// the modules main.js uses.
describe("index.html pre-paint script", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const match = html.match(/<script>([\s\S]*?)<\/script>/);
  const source = match ? match[1] : "";

  function runInlineScript({ search = "", storage = null, storageThrows = false } = {}) {
    const root = fakeRoot();
    const win = { location: { search } };
    Object.defineProperty(win, "localStorage", {
      get() {
        if (storageThrows) throw new Error("SecurityError");
        return storage;
      },
    });
    new Function("window", "document", source)(win, { documentElement: root });
    return root;
  }

  it("is present", () => {
    expect(source).toContain("stormiq:ui");
    expect(source).toContain("stormiq:theme");
  });

  const cases = [
    { name: "nothing saved, no query", search: "", saved: {} },
    { name: "?ui=v1", search: "?ui=v1", saved: {} },
    { name: "?ui=V1 (any case)", search: "?ui=V1", saved: {} },
    { name: "?ui=%20v1%20 (spaces)", search: "?ui=%20v1%20", saved: {} },
    { name: "?ui=Classic with v1 saved", search: "?ui=Classic", saved: { [UI_STORAGE_KEY]: "v1" } },
    { name: "v1 saved, no query", search: "", saved: { [UI_STORAGE_KEY]: "v1" } },
    { name: "unknown query, v1 saved", search: "?ui=v2", saved: { [UI_STORAGE_KEY]: "v1" } },
    { name: "unknown saved value", search: "", saved: { [UI_STORAGE_KEY]: "v9" } },
    { name: "dark saved", search: "", saved: { [THEME_STORAGE_KEY]: "dark" } },
    { name: "light saved, v1 query", search: "?ui=v1", saved: { [THEME_STORAGE_KEY]: "light" } },
    { name: "invalid theme saved", search: "", saved: { [THEME_STORAGE_KEY]: "neon" } },
  ];

  for (const testCase of cases) {
    it(`agrees with uiVersion.js and theme.js: ${testCase.name}`, () => {
      const root = runInlineScript({ search: testCase.search, storage: memoryStorage(testCase.saved) });
      const expectedUi = resolveUiVersion({ search: testCase.search, storage: memoryStorage(testCase.saved) });
      expect(root.getAttribute("data-ui")).toBe(expectedUi === "v1" ? "v1" : null);
      expect(root.getAttribute("data-theme")).toBe(readStoredTheme(memoryStorage(testCase.saved)));
    });
  }

  it("still applies ?ui=v1 when storage throws", () => {
    const root = runInlineScript({ search: "?ui=v1", storageThrows: true });
    expect(root.getAttribute("data-ui")).toBe("v1");
    expect(root.getAttribute("data-theme")).toBeNull();
    const throwing = runInlineScript({ search: "", storage: throwingStorage() });
    expect(throwing.getAttribute("data-ui")).toBeNull();
  });
});

// useTheme.js: the Vue wrapper keeps one controller per page and copies its
// state into refs, which the theme toggle's label and icon read.
describe("useTheme", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("shares one controller and updates its refs on toggle and system changes", async () => {
    const root = fakeRoot();
    const storage = memoryStorage();
    const matchMedia = fakeMatchMedia(false);
    // A fresh module (its controller is per page), loaded before the fake
    // globals so Vue itself doesn't see them; useTheme reads them lazily.
    vi.resetModules();
    const { useTheme } = await import("../src/design/useTheme.js");
    vi.stubGlobal("window", { localStorage: storage, matchMedia });
    vi.stubGlobal("document", { documentElement: root });

    const first = useTheme();
    const second = useTheme();
    expect(matchMedia).toHaveBeenCalledOnce();
    expect(first.theme.value).toBe("light");
    expect(first.choice.value).toBeNull();

    first.toggle();
    expect(second.theme.value).toBe("dark");
    expect(second.choice.value).toBe("dark");
    expect(root.getAttribute("data-theme")).toBe("dark");
    expect(storage.data.get(THEME_STORAGE_KEY)).toBe("dark");

    second.setChoice(null);
    matchMedia.change(true);
    expect(first.theme.value).toBe("dark");
    expect(first.choice.value).toBeNull();
    expect(root.getAttribute("data-theme")).toBeNull();
  });
});
