import { describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import { durationLabel, makeLabel, sizeLabel, videoView } from "../src/followup/surpriseVideo";
import { createSurpriseShow } from "../src/followup/useSurpriseShow";
import { SHOW_STEPS, showSteps } from "../src/report/progress";

function deferred() {
  let resolve;
  const promise = new Promise((done) => (resolve = done));
  return { promise, resolve };
}

const READY = {
  state: "ready",
  beats: [{ index: 0, kind: "title", text: "A take" }],
  voice: { label: "Cedar", fellBack: true },
  video: { state: "ready", durationS: 117.6, sizeBytes: 8_011_000, version: "0123456789abcdef" },
};

describe("the video output's states", () => {
  it("plays the video once it is made from the show as it is", () => {
    const view = videoView(READY);
    expect(view.mode).toBe("ready");
    expect(view.version).toBe("0123456789abcdef");
    expect(view.fellBack).toBe(true);
    expect(view.voiceLabel).toBe("Cedar");
    expect(durationLabel(view.durationS)).toBe("1:58");
    expect(sizeLabel(view.sizeBytes)).toBe("7.6 MB");
  });

  it("shows the loader while the show job or the render runs, or the click is on its way", () => {
    expect(videoView({ state: "running", progress: { stage: "voice", done: 3, total: 12, percent: 40 } }).mode).toBe("making");
    expect(videoView({ state: "pending" }).mode).toBe("making");
    expect(videoView({ state: "missing" }, { starting: true }).mode).toBe("making");
    expect(videoView({ state: "running", progress: { stage: "rendering", percent: 78 } }).percent).toBe(78);
  });

  it("says why it failed and offers Try again", () => {
    const failed = videoView({ state: "error", error: "The video took too long to render its pictures. Please try again.", beats: [{}] });
    expect(failed.mode).toBe("failed");
    expect(failed.failure).toMatch(/took too long/);
    expect(makeLabel(failed)).toBe("Try again");
    const refused = videoView({ state: "missing" }, { error: "Surprise me is turned off for your organization." });
    expect(refused.mode).toBe("failed");
  });

  it("offers to make the video, or the whole show when there is no take yet", () => {
    expect(makeLabel(videoView({ state: "missing", beats: [{}] }))).toBe("Make the video");
    expect(makeLabel(videoView({ state: "none", beats: [] }))).toBe("Make the show");
    expect(videoView({ state: "ready", video: { state: "none" } }).mode, "ready only with its video").toBe("idle");
    expect(videoView(null).mode).toBe("idle");
  });

  it("leaves unknown lengths and sizes unsaid", () => {
    expect(durationLabel(null)).toBe("");
    expect(sizeLabel(0)).toBe("");
    expect(sizeLabel(2048)).toBe("2 KB");
  });
});

describe("the show's one loader", () => {
  it("goes from the take to the rendered video", () => {
    expect(SHOW_STEPS.map((step) => step.label)).toEqual([
      "Waiting for a free worker",
      "Writing the take",
      "Writing the script",
      "Recording the voice",
      "Timing",
      "Directing the shots",
      "Drawing the pictures",
      "Rendering the video",
    ]);
    const voice = showSteps({ stage: "voice", done: 3, total: 12 });
    expect(voice[3]).toEqual({ label: "Recording the voice", state: "current", detail: "3 of 12 lines" });
    expect(voice.slice(0, 3).every((step) => step.state === "done")).toBe(true);
    const mixing = showSteps({ stage: "mixing" });
    expect(mixing[7]).toEqual({ label: "Rendering the video", state: "current", detail: "Mixing the sound" });
    expect(showSteps(null)[0].state).toBe("current");
  });
});

describe("the video's address", () => {
  it("is the session's MP4, a new URL for a new video, and named for saving on download", () => {
    expect(api.getSurpriseVideoUrl("s 1")).toBe("/api/sessions/s%201/surprise-show/video.mp4");
    expect(api.getSurpriseVideoUrl("s1", "abc")).toBe("/api/sessions/s1/surprise-show/video.mp4?v=abc");
    expect(api.getSurpriseVideoUrl("s1", "abc", { download: true })).toBe("/api/sessions/s1/surprise-show/video.mp4?v=abc&download=1");
  });
});

describe("the show's data", () => {
  it("reads the show and starts nothing on its own", async () => {
    const fake = { getSurpriseShow: vi.fn(async () => ({ state: "missing", beats: [] })), startSurpriseShow: vi.fn() };
    const show = createSurpriseShow({ api: fake });
    show.open("s1");
    await vi.waitFor(() => expect(show.state.body?.state).toBe("missing"));
    expect(fake.getSurpriseShow).toHaveBeenCalledWith("s1", expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(fake.startSurpriseShow).not.toHaveBeenCalled();
    show.close();
  });

  it("starts on a click and follows the show and its render until the video is made", async () => {
    vi.useFakeTimers();
    try {
      const fake = {
        getSurpriseShow: vi.fn(),
        startSurpriseShow: vi.fn(async () => ({ state: "pending" })),
      };
      fake.getSurpriseShow
        .mockResolvedValueOnce({ state: "missing" })
        .mockResolvedValueOnce({ state: "running", progress: { stage: "directing" } })
        .mockResolvedValueOnce({ state: "running", progress: { stage: "rendering" }, video: { state: "running" } })
        .mockResolvedValue(READY);
      const show = createSurpriseShow({ api: fake });
      show.open("s1");
      await vi.waitFor(() => expect(show.state.body?.state).toBe("missing"));
      await show.start();
      expect(fake.startSurpriseShow).toHaveBeenCalledTimes(1);
      await vi.advanceTimersByTimeAsync(1500);
      expect(show.state.body.progress.stage).toBe("directing");
      await vi.advanceTimersByTimeAsync(1500);
      expect(videoView(show.state.body).steps[7].state).toBe("current");
      await vi.advanceTimersByTimeAsync(1500);
      expect(videoView(show.state.body).mode).toBe("ready");
      const calls = fake.getSurpriseShow.mock.calls.length;
      await vi.advanceTimersByTimeAsync(10000);
      expect(fake.getSurpriseShow.mock.calls.length, "no poll once the video is made").toBe(calls);
      show.close();
    } finally {
      vi.useRealTimers();
    }
  });

  it("rides out a blip while it polls, and stops on another error", async () => {
    vi.useFakeTimers();
    try {
      const blip = Object.assign(new Error("Bad gateway"), { status: 502 });
      const gone = Object.assign(new Error("No cached session"), { status: 404 });
      const fake = {
        getSurpriseShow: vi
          .fn()
          .mockResolvedValueOnce({ state: "running" })
          .mockRejectedValueOnce(blip)
          .mockResolvedValueOnce({ state: "running" })
          .mockRejectedValueOnce(gone),
        startSurpriseShow: vi.fn(),
      };
      const show = createSurpriseShow({ api: fake });
      show.open("s1");
      await vi.waitFor(() => expect(show.state.body?.state).toBe("running"));
      await vi.advanceTimersByTimeAsync(1500);
      expect(show.state.error).toBe("");
      await vi.advanceTimersByTimeAsync(3000);
      await vi.advanceTimersByTimeAsync(1500);
      expect(show.state.error).toBe("No cached session");
      const calls = fake.getSurpriseShow.mock.calls.length;
      await vi.advanceTimersByTimeAsync(30000);
      expect(fake.getSurpriseShow.mock.calls.length).toBe(calls);
      show.close();
    } finally {
      vi.useRealTimers();
    }
  });

  it("says why a click failed, and drops late answers for a session it left", async () => {
    const late = deferred();
    const fake = {
      getSurpriseShow: vi.fn((id) => (id === "s1" ? late.promise : Promise.resolve({ state: "missing" }))),
      startSurpriseShow: vi.fn(async () => {
        throw Object.assign(new Error("Surprise me is turned off for your organization."), { status: 403 });
      }),
    };
    const show = createSurpriseShow({ api: fake });
    show.open("s1");
    const signal = fake.getSurpriseShow.mock.calls[0][1].signal;
    show.open("s2");
    expect(signal.aborted).toBe(true);
    await vi.waitFor(() => expect(show.state.body?.state).toBe("missing"));
    late.resolve(READY);
    await Promise.resolve();
    expect(show.state.body.state).toBe("missing");
    await show.start();
    expect(videoView(show.state.body, { error: show.state.error }).failure).toMatch(/turned off/);
    expect(show.state.starting).toBe(false);
    show.close();
  });
});
