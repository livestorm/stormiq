// When the v1 shell shows the signed-out landing page (src/design/landing.js).

import { describe, expect, it } from "vitest";
import { PUBLIC_PATHS, showsLanding } from "../src/design/landing";

describe("showsLanding", () => {
  it("shows the landing page to a visitor who isn't signed in, on any page", () => {
    for (const path of ["/sessions", "/search", "/sessions/abc/report", "/admin"]) {
      expect(showsLanding({ authReady: true, authed: false, path })).toBe(true);
    }
  });

  it("never shows it to someone signed in", () => {
    expect(showsLanding({ authReady: true, authed: true, path: "/sessions" })).toBe(false);
  });

  it("waits for the bootstrap, so a signed-in user never sees it flash", () => {
    expect(showsLanding({ authReady: false, authed: false, path: "/sessions" })).toBe(false);
  });

  it("leaves the public pages open", () => {
    expect([...PUBLIC_PATHS]).toEqual(["/beta-info", "/design", "/auth/callback"]);
    for (const path of PUBLIC_PATHS) expect(showsLanding({ authReady: true, authed: false, path })).toBe(false);
  });
});
