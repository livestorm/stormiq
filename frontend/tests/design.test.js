import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  THEME_STORAGE_KEY,
  createThemeController,
  readStoredTheme,
  resolveTheme,
  safeStorage,
  writeStoredTheme,
} from "../src/design/theme.js";

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

describe("safeStorage", () => {
  it("returns localStorage, or null when it is missing or its getter throws", () => {
    const storage = memoryStorage();
    expect(safeStorage({ localStorage: storage })).toBe(storage);
    expect(safeStorage({})).toBeNull();
    const blocked = {};
    Object.defineProperty(blocked, "localStorage", {
      get() {
        throw new Error("SecurityError");
      },
    });
    expect(safeStorage(blocked)).toBeNull();
    expect(safeStorage(undefined)).toBeNull();
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

// The inline script in index.html repeats the theme logic so the
// first paint is right. Run it against fake globals and check it agrees with
// the modules main.js uses.
describe("index.html pre-paint script", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const match = html.match(/<script>([\s\S]*?)<\/script>/);
  const source = match ? match[1] : "";

  function runInlineScript({ storage = null, storageThrows = false } = {}) {
    const root = fakeRoot();
    const win = {};
    Object.defineProperty(win, "localStorage", {
      get() {
        if (storageThrows) throw new Error("SecurityError");
        return storage;
      },
    });
    new Function("window", "document", source)(win, { documentElement: root });
    return root;
  }

  it("marks every page for the design's CSS, with no switch left", () => {
    expect(html).toMatch(/<html lang="en" data-ui="v1">/);
    expect(source).toContain("stormiq:theme");
    expect(source).not.toContain("stormiq:ui");
  });

  for (const saved of [{}, { [THEME_STORAGE_KEY]: "dark" }, { [THEME_STORAGE_KEY]: "light" }, { [THEME_STORAGE_KEY]: "neon" }]) {
    it(`agrees with theme.js: ${JSON.stringify(saved)}`, () => {
      const root = runInlineScript({ storage: memoryStorage(saved) });
      expect(root.getAttribute("data-theme")).toBe(readStoredTheme(memoryStorage(saved)));
    });
  }

  it("follows the system when storage throws", () => {
    expect(runInlineScript({ storageThrows: true }).getAttribute("data-theme")).toBeNull();
    expect(runInlineScript({ storage: throwingStorage() }).getAttribute("data-theme")).toBeNull();
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
