import { describe, expect, it } from "vitest";
import { createOutputVersions } from "../src/followup/useOutputVersions";

// A fake api whose answers the test releases one at a time.
function fakeApi() {
  const calls = [];
  return {
    calls,
    getOutputVersions(sessionId, query, { signal }) {
      return new Promise((resolve, reject) => {
        calls.push({ sessionId, query, signal, resolve, reject });
      });
    },
  };
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("earlier versions' data", () => {
  it("loads one output's versions with its own signal, once per output", async () => {
    const api = fakeApi();
    const versions = createOutputVersions({ api });
    versions.open("s1", { kind: "faq", language: "English" });
    versions.open("s1", { kind: "faq", language: "English" });
    expect(api.calls).toHaveLength(1);
    expect(api.calls[0].signal).toBeInstanceOf(AbortSignal);
    api.calls[0].resolve({ versions: [{ id: 1 }] });
    await flush();
    expect(versions.state.body.versions).toEqual([{ id: 1 }]);
    expect(versions.state.loading).toBe(false);
  });

  it("drops a late answer for the output it left", async () => {
    const api = fakeApi();
    const versions = createOutputVersions({ api });
    versions.open("s1", { kind: "faq", language: "English" });
    versions.open("s1", { kind: "faq", language: "French" });
    expect(api.calls[0].signal.aborted).toBe(true);
    api.calls[0].resolve({ versions: [{ id: "old" }] });
    api.calls[1].resolve({ versions: [{ id: "new" }] });
    await flush();
    expect(versions.state.body.versions).toEqual([{ id: "new" }]);
  });

  it("asks nothing for the desk or the chapters, and reloads after a save", () => {
    const api = fakeApi();
    const versions = createOutputVersions({ api });
    versions.open("s1", null);
    expect(api.calls).toHaveLength(0);
    expect(versions.state.body).toBe(null);
    versions.open("s1", { kind: "overall", language: "English" });
    versions.reload();
    expect(api.calls).toHaveLength(2);
    expect(api.calls[0].signal.aborted).toBe(true);
  });

  it("keeps the error, never a late one, and stops on close", async () => {
    const api = fakeApi();
    const versions = createOutputVersions({ api });
    versions.open("s1", { kind: "faq", language: "English" });
    api.calls[0].reject(new Error("The earlier versions could not be read."));
    await flush();
    expect(versions.state.error).toBe("The earlier versions could not be read.");
    versions.reload();
    versions.close();
    expect(api.calls[1].signal.aborted).toBe(true);
    api.calls[1].reject(new Error("late"));
    await flush();
    expect(versions.state.error).toBe("The earlier versions could not be read.");
  });
});
