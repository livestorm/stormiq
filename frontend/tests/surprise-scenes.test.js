import { describe, expect, it, vi } from "vitest";
import { surpriseBeats } from "../src/followup/surpriseBeats";
import { beatMs, captionWords, clockSeconds, curveGeometry, revealedChars, sceneFor } from "../src/followup/surpriseScenes";
import { createSurpriseVoice } from "../src/followup/useSurpriseVoice";
import { voiceSteps } from "../src/report/progress";

// A 30-minute session: a quiet chat with its busiest minute at 23.
const MINUTES = Array.from({ length: 30 }, (_, minute) => ({ minute, audienceChat: minute === 23 ? 50 : minute % 4 }));

function beat(text, kind = "line") {
  return { ...surpriseBeats(text)[0], kind };
}

describe("a scene for each beat, from the session's own data", () => {
  it("draws the chat curve and lights up the minute a time names", () => {
    expect(sceneFor(beat("At 23:00 the room finally relaxed."), { minutes: MINUTES })).toEqual({
      type: "curve",
      minute: 23,
      label: "23:00",
      punch: false,
    });
  });

  it("lights up the busiest minute for a peak, without a number of its own", () => {
    expect(sceneFor(beat("Then the chat peaked, hard."), { minutes: MINUTES })).toEqual({ type: "curve", minute: 23, label: null, punch: false });
    expect(sceneFor(beat("C'était le pic de la session."), { minutes: MINUTES }).type).toBe("curve");
  });

  it("never draws a curve the session doesn't have", () => {
    const quiet = MINUTES.map((row) => ({ ...row, audienceChat: 0 }));
    expect(sceneFor(beat("At 23:00 nothing moved."), { minutes: quiet }).type).toBe("words");
    expect(sceneFor(beat("At 45:00 it was over."), { minutes: MINUTES }).type, "a time after the last minute").toBe("words");
    expect(sceneFor(beat("At 23:00 it began."), { minutes: [] }).type).toBe("words");
  });

  it("fills a ring to a percentage said, and counts a number up", () => {
    expect(sceneFor(beat("56% stayed for pricing."), { minutes: MINUTES })).toEqual({ type: "ring", value: 56, label: "56%", punch: false });
    expect(sceneFor(beat("The chat sent 1,200 messages."), { minutes: MINUTES })).toEqual({ type: "count", label: "1,200", punch: false });
    expect(sceneFor(beat("Only 1 person asked."), { minutes: MINUTES }).type, "a count of one doesn't count up").toBe("words");
  });

  it("gives a punchline its card and anything else its words", () => {
    expect(sceneFor(beat("Nobody believed it.", "punch"), { minutes: MINUTES })).toEqual({ type: "punch", punch: true });
    expect(sceneFor(beat("A quiet room.", "title"), { minutes: MINUTES })).toEqual({ type: "words", title: true, punch: false });
    expect(sceneFor(beat("99% of it worked.", "punch"), { minutes: MINUTES })).toMatchObject({ type: "ring", punch: true });
  });

  it("reads clock times on the session clock", () => {
    expect(clockSeconds("23:00")).toBe(1380);
    expect(clockSeconds("1:02:05")).toBe(3725);
    expect(clockSeconds("23:75")).toBe(null);
    expect(clockSeconds("56%")).toBe(null);
  });

  it("places the minute on the curve", () => {
    const geometry = curveGeometry(MINUTES, 23);
    expect(geometry.line.startsWith("M")).toBe(true);
    expect(geometry.area.endsWith("Z")).toBe(true);
    expect(geometry.x).toBeCloseTo((23.5 / 30) * 1000, 0);
    expect(geometry.y, "the busiest minute is at the top").toBe(16);
    expect(curveGeometry(MINUTES, 99)).toBe(null);
  });
});

describe("captions in step with the clip", () => {
  it("keeps every word in its place, so the words make the beat", () => {
    const { text, parts } = beat("At 23:00 the room relaxed.");
    const words = captionWords(parts);
    expect(words.map((word) => word.text).join("")).toBe(text);
    expect(words.find((word) => word.kind === "number")).toMatchObject({ text: "23:00", start: 3, end: 8 });
  });

  it("reveals the text at the clip's pace", () => {
    expect(revealedChars(40, 0)).toBe(0);
    expect(revealedChars(40, 0.5)).toBe(20);
    expect(revealedChars(40, 2)).toBe(40);
    expect(revealedChars(40, Number.NaN)).toBe(0);
  });

  it("times a beat by its clip, or a reading pace without one", () => {
    expect(beatMs({ text: "x", durationS: 2.5 })).toBe(2500);
    expect(beatMs({ text: "one two three" })).toBe(1800);
    expect(beatMs({ text: Array(10).fill("word").join(" ") })).toBe(3800);
  });
});

describe("the voice loader's steps", () => {
  it("says how many sentences are recorded", () => {
    const steps = voiceSteps({ stage: "speaking", done: 3, total: 12 });
    expect(steps.map((step) => step.state)).toEqual(["done", "done", "current"]);
    expect(steps[2].detail).toBe("3 of 12 sentences");
    expect(voiceSteps(null)[0].state).toBe("current");
  });
});

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

describe("the voice's data", () => {
  it("reads the voice and starts nothing on its own", async () => {
    const api = { getSurpriseVoice: vi.fn(async () => ({ state: "missing", beats: [] })), startSurpriseVoice: vi.fn() };
    const voice = createSurpriseVoice({ api });
    voice.open("s1");
    await vi.waitFor(() => expect(voice.state.body?.state).toBe("missing"));
    expect(api.getSurpriseVoice).toHaveBeenCalledWith("s1", expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(api.startSurpriseVoice).not.toHaveBeenCalled();
    voice.close();
  });

  it("starts on a click and follows the job until the clips are made", async () => {
    vi.useFakeTimers();
    try {
      const answers = [{ state: "running", progress: { stage: "speaking", done: 1, total: 3 } }, { state: "ready", beats: [{ clip: "k" }] }];
      const api = {
        getSurpriseVoice: vi.fn(async () => answers.shift() || { state: "ready" }),
        startSurpriseVoice: vi.fn(async () => ({ state: "pending" })),
      };
      const voice = createSurpriseVoice({ api });
      voice.open("s1");
      await vi.waitFor(() => expect(voice.state.body?.state).toBe("running"));
      api.getSurpriseVoice.mockClear();
      answers.splice(0, answers.length, { state: "running" }, { state: "ready", beats: [] });
      await voice.start();
      expect(api.startSurpriseVoice).toHaveBeenCalledTimes(1);
      expect(voice.state.body.state).toBe("pending");
      await vi.advanceTimersByTimeAsync(1500);
      expect(voice.state.body.state).toBe("running");
      await vi.advanceTimersByTimeAsync(1500);
      expect(voice.state.body.state).toBe("ready");
      await vi.advanceTimersByTimeAsync(10000);
      expect(api.getSurpriseVoice, "no poll once ready").toHaveBeenCalledTimes(2);
      voice.close();
    } finally {
      vi.useRealTimers();
    }
  });

  it("rides out a blip while it polls, and stops on another error", async () => {
    vi.useFakeTimers();
    try {
      const blip = Object.assign(new Error("Bad gateway"), { status: 502 });
      const gone = Object.assign(new Error("No cached session"), { status: 404 });
      const api = {
        getSurpriseVoice: vi
          .fn()
          .mockResolvedValueOnce({ state: "running" })
          .mockRejectedValueOnce(blip)
          .mockResolvedValueOnce({ state: "running" })
          .mockRejectedValueOnce(gone),
        startSurpriseVoice: vi.fn(),
      };
      const voice = createSurpriseVoice({ api });
      voice.open("s1");
      await vi.waitFor(() => expect(voice.state.body?.state).toBe("running"));
      await vi.advanceTimersByTimeAsync(1500);
      expect(voice.state.error).toBe("");
      await vi.advanceTimersByTimeAsync(3000);
      await vi.advanceTimersByTimeAsync(1500);
      expect(voice.state.error).toBe("No cached session");
      const calls = api.getSurpriseVoice.mock.calls.length;
      await vi.advanceTimersByTimeAsync(30000);
      expect(api.getSurpriseVoice.mock.calls.length).toBe(calls);
      voice.close();
    } finally {
      vi.useRealTimers();
    }
  });

  it("says why Play failed, and drops late answers for a session it left", async () => {
    const late = deferred();
    const api = {
      getSurpriseVoice: vi.fn((id) => (id === "s1" ? late.promise : Promise.resolve({ state: "missing" }))),
      startSurpriseVoice: vi.fn(async () => {
        throw Object.assign(new Error("Surprise me is turned off for your organization."), { status: 403 });
      }),
    };
    const voice = createSurpriseVoice({ api });
    voice.open("s1");
    const signal = api.getSurpriseVoice.mock.calls[0][1].signal;
    voice.open("s2");
    expect(signal.aborted).toBe(true);
    await vi.waitFor(() => expect(voice.state.body?.state).toBe("missing"));
    late.resolve({ state: "ready" });
    await Promise.resolve();
    expect(voice.state.body.state).toBe("missing");
    await voice.start();
    expect(voice.state.error).toMatch(/turned off/);
    expect(voice.state.starting).toBe(false);
    voice.close();
  });
});
