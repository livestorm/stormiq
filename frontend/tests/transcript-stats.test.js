import { describe, expect, it } from "vitest";
import record from "./fixtures/record-v1.json";
import transcript from "./fixtures/transcript-v1.json";
import { formatDuration, speakingStats, whyHidden } from "../src/transcript/stats";

const measures = record.record.measures;

describe("the Speaking stats card", () => {
  it("words the server's pace, stretch and talk time, with the saved names", () => {
    const stats = speakingStats(measures, transcript.speakers);
    expect(stats.pace).toEqual({ text: "135 words a minute", status: "measured" });
    expect(stats.stretch).toEqual({ text: "8 s", who: "Nora Vale", status: "measured" });
    expect(stats.speakers.map((row) => row.name)).toEqual(["Nora Vale", "Rhea Stone", "Speaker 3"]);
    expect(stats.speakers[0]).toEqual({
      key: "0",
      name: "Nora Vale",
      time: "10 min 26 s",
      share: "52%",
      bar: measures["m.speaker_talk_time"].rows[0].share * 100,
      pace: "135 wpm",
    });
    expect(stats.summary).toBe("135 words a minute · Nora Vale spoke 52% of the time");
    expect(stats.chip).toBe("3 speakers");
    expect(stats.estimated).toBe(false);
  });

  it("keeps a hidden number hidden and says why", () => {
    const hidden = {
      "m.speaking_pace": { state: "insufficient", value: null, gate: { field: "pace_ok", minimum: 1, actual: 0 } },
      "m.speaker_talk_time": {
        state: "insufficient",
        value: null,
        den: 1,
        rows: [{ key: "unknown", speakingS: null, share: null }],
        gate: { field: "shares_ok", minimum: 1, actual: 0 },
      },
      "m.longest_stretch": { state: "insufficient", value: null, gate: { field: "speaking_s", minimum: 60, actual: 30 } },
    };
    const stats = speakingStats(hidden, []);
    expect(stats.pace).toEqual({ why: "The transcript covers too little of the recording for a pace." });
    expect(stats.speakers).toEqual([]);
    expect(stats.speakersWhy).toBe("The speakers couldn't be told apart reliably.");
    expect(stats.stretch).toEqual({ why: "There's less than a minute of speech." });
    expect(stats.summary).toBe("The transcript covers too little of the recording for a pace.");
    expect(stats.chip).toBe("");
  });

  it("marks an approximation as estimated", () => {
    const approx = { ...measures, "m.speaker_talk_time": { ...measures["m.speaker_talk_time"], state: "approximation" } };
    const stats = speakingStats(approx, transcript.speakers);
    expect(stats.estimated).toBe(true);
    expect(stats.speakers).toHaveLength(3);
  });

  it("names speech without a speaker as unattributed", () => {
    const talk = { state: "ok", value: 1, den: 1, rows: [{ key: "unknown", speakingS: 90, share: 1, words: 200, wpm: 133.3 }] };
    expect(speakingStats({ "m.speaker_talk_time": talk }, []).speakers[0].name).toBe("Unattributed speech");
  });

  it("says why when there is no transcript or no measure", () => {
    expect(whyHidden({ state: "unavailable", coverage: { missing: ["transcript"] } })).toBe("No transcript yet.");
    expect(whyHidden(undefined)).toBe("Not computed for this session yet.");
    expect(whyHidden({ state: "ok", value: 1 })).toBe("");
  });

  it("formats durations", () => {
    expect(formatDuration(7.55)).toBe("8 s");
    expect(formatDuration(125)).toBe("2 min 5 s");
    expect(formatDuration(120)).toBe("2 min");
    expect(formatDuration(3780)).toBe("1 h 3 min");
    expect(formatDuration(null)).toBe("");
  });
});
