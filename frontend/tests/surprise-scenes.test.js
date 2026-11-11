import { describe, expect, it, vi } from "vitest";
import { beatsWithParts, surpriseBeats } from "../src/followup/surpriseBeats";
import {
  EFFECTS,
  MAX_HEADLINE_WORDS,
  beatMs,
  clockLabel,
  clockSeconds,
  curveGeometry,
  headlineWords,
  percentValue,
  ruleHeadline,
  ruleScene,
  storyScene,
} from "../src/followup/surpriseScenes";
import { createSurpriseVoice } from "../src/followup/useSurpriseVoice";
import { voiceSteps } from "../src/report/progress";

// A 30-minute session: a quiet chat with its busiest minute at 23.
const MINUTES = Array.from({ length: 30 }, (_, minute) => ({ minute, audienceChat: minute === 23 ? 50 : minute % 4 }));

function beat(text, kind = "line") {
  return { ...surpriseBeats(text)[0], kind };
}

function inBeat(headline, text) {
  const words = (value) => value.toLowerCase().split(/\s+/).map((w) => w.replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}%]+$/gu, "")).filter(Boolean);
  const head = words(headline);
  const all = words(text);
  return all.some((_, start) => head.every((word, i) => all[start + i] === word));
}

describe("the page's own scenes, when the server sent none", () => {
  it("draws the chat curve to the minute a time names, or the busiest one for a peak", () => {
    expect(ruleScene(beat("At 23:00 the room finally relaxed."), { minutes: MINUTES })).toMatchObject({
      effect: "pulse",
      minute: 23,
      label: "23:00",
    });
    expect(ruleScene(beat("Then the chat peaked, hard."), { minutes: MINUTES })).toMatchObject({ effect: "pulse", minute: 23, label: null });
    expect(ruleScene(beat("C'était le pic de la session."), { minutes: MINUTES }).effect).toBe("pulse");
  });

  it("never draws a curve the session doesn't have", () => {
    const quiet = MINUTES.map((row) => ({ ...row, audienceChat: 0 }));
    expect(ruleScene(beat("At 23:00 nothing moved."), { minutes: quiet }).effect).toBe("pop");
    expect(ruleScene(beat("At 45:00 it was over."), { minutes: MINUTES }).effect, "a time after the last minute").toBe("pop");
    expect(ruleScene(beat("At 23:00 it began."), { minutes: [] }).effect).toBe("pop");
  });

  it("counts up a number the beat says, with a ring for a percentage", () => {
    expect(ruleScene(beat("56% stayed for pricing."), { minutes: MINUTES })).toMatchObject({ effect: "count", number: "56%" });
    expect(ruleScene(beat("The chat sent 1,200 messages."), { minutes: MINUTES })).toMatchObject({ effect: "count", number: "1,200" });
    expect(ruleScene(beat("Only 1 person asked."), { minutes: MINUTES }).effect, "a count of one doesn't count up").toBe("pop");
    expect(percentValue("56%")).toBe(56);
    expect(percentValue("140%")).toBe(null);
    expect(percentValue("56")).toBe(null);
  });

  it("slams the title in, flashes or stamps a punchline, and pops the rest", () => {
    expect(ruleScene(beat("A quiet room", "title")).effect).toBe("slam");
    expect(ruleScene(beat("Nobody believed it.", "punch"), { index: 2 })).toMatchObject({ effect: "flash", punch: true });
    expect(ruleScene(beat("Nobody believed it.", "punch"), { index: 3 }).effect).toBe("stamp");
    expect(ruleScene(beat("The host opened the slides.")).effect).toBe("pop");
  });

  it("lifts a short headline word for word from the beat, never the whole sentence", () => {
    const sentences = [
      "Amara clicked connect, then clicked it again, just to be sure the SSO really worked.",
      "The host said one more slide, and then said it again, and again, with feeling.",
      "Nobody believed it.",
    ];
    for (const text of sentences) {
      const item = beatsWithParts([{ kind: "line", text }], ["Single sign-on and SSO"])[0];
      const headline = ruleHeadline(item);
      expect(headlineWords(headline).length).toBeLessThanOrEqual(MAX_HEADLINE_WORDS);
      expect(headline.length).toBeGreaterThan(0);
      expect(inBeat(headline, text), `${headline} is in "${text}"`).toBe(true);
    }
    expect(ruleHeadline(beat("Nobody believed it."))).toBe("Nobody believed it");
    const sso = beatsWithParts([{ kind: "line", text: sentences[0] }], ["Single sign-on and SSO"])[0];
    expect(ruleHeadline(sso)).toContain("SSO");
  });

  it("reads clock times on the session clock and writes them back", () => {
    expect(clockSeconds("23:00")).toBe(1380);
    expect(clockSeconds("1:02:05")).toBe(3725);
    expect(clockSeconds("23:75")).toBe(null);
    expect(clockSeconds("56%")).toBe(null);
    expect([clockLabel(1385), clockLabel(3725), clockLabel(null)]).toEqual(["23:05", "1:02:05", ""]);
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

describe("the server's storyboard", () => {
  const quote = { id: "R2", kind: "question", t: 1500, text: "Is SSO included?" };

  it("plays the server's checked scene for a beat", () => {
    const item = { ...beat("The chat sent 50 messages in three minutes!"), scene: { effect: "count", headline: "50 messages", number: "50", quote: null } };
    expect(storyScene(item, { minutes: MINUTES })).toEqual({ effect: "count", headline: "50 messages", number: "50", quote: null, minute: null, label: null, punch: false });
    const chat = { ...beat("Then someone asked the only question that mattered.", "punch"), scene: { effect: "chat", headline: "", number: null, quote } };
    expect(storyScene(chat)).toMatchObject({ effect: "chat", quote: { kind: "question", t: 1500, text: "Is SSO included?" }, punch: true });
  });

  it("draws a pulse on the session's curve, and slams it in when there is none", () => {
    const item = { ...beat("The chat woke up."), scene: { effect: "pulse", headline: "The chat woke up", number: null, quote: null } };
    expect(storyScene(item, { minutes: MINUTES })).toMatchObject({ effect: "pulse", minute: 23 });
    expect(storyScene(item, { minutes: [] })).toMatchObject({ effect: "slam", headline: "The chat woke up" });
  });

  it("falls back to the page's own scene when the server's is missing or broken", () => {
    const text = "56% stayed for pricing.";
    for (const scene of [null, { effect: "wobble", headline: "x" }, { effect: "chat", headline: "", quote: null }, { effect: "count", headline: "", number: null }, { effect: "pop", headline: " " }]) {
      expect(storyScene({ ...beat(text), scene }, { minutes: MINUTES }), JSON.stringify(scene)).toEqual(ruleScene(beat(text), { minutes: MINUTES }));
    }
  });

  it("knows the server's effects", () => {
    expect(EFFECTS).toEqual(["slam", "stamp", "pop", "count", "chat", "pulse", "flash"]);
  });
});

describe("timing without the voice", () => {
  it("times a beat by its clip, or a reading pace without one", () => {
    expect(beatMs({ text: "x", durationS: 2.5 })).toBe(2500);
    expect(beatMs({ text: "one two three" })).toBe(1800);
    expect(beatMs({ text: Array(10).fill("word").join(" ") })).toBe(3800);
  });
});

describe("the voice loader's steps", () => {
  it("plans the scenes, then says how many sentences are recorded", () => {
    expect(voiceSteps({ stage: "storyboard" }).map((step) => step.state)).toEqual(["done", "done", "current", "todo"]);
    const steps = voiceSteps({ stage: "speaking", done: 3, total: 12 });
    expect(steps.map((step) => step.state)).toEqual(["done", "done", "done", "current"]);
    expect(steps[3].detail).toBe("3 of 12 sentences");
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
