// The v1 Report's "Method and data quality" wording (src/report/method.js),
// on the synthetic record fixtures and on hand-made states: what the numbers
// rest on, and every hidden number with the server's reason.

import { describe, expect, it } from "vitest";
import small from "./fixtures/record-v1.json";
import crowd from "./fixtures/record-v1-crowd.json";
import {
  authorsRow,
  clockRow,
  gateReason,
  hiddenWhy,
  labelsRow,
  methodRows,
  methodSummary,
  notShown,
  peopleRow,
  transcriptRow,
} from "../src/report/method";

describe("methodRows", () => {
  it("lists the transcript, the clock, the people, who wrote and the chat sorting", () => {
    const rows = methodRows(small);
    expect(rows.map((row) => row.key)).toEqual(["transcript", "clock", "people", "authors", "labels"]);
    expect(rows[0]).toMatchObject({ value: "Good", tone: "good" });
    expect(rows[0].details).toEqual([
      "Mean word confidence 0.92; 1% of words unclear.",
      "Speech covers 67% of the recording.",
      "Speakers: 2 found, 2 expected.",
      "Spoken: English.",
    ]);
    expect(rows[1]).toMatchObject({ value: "Verified", tone: "good" });
    expect(rows[1].details[0]).toBe("Speech and chat line up on 3 moments. Moment times ±6 s.");
    expect(rows[1].details[1]).toBe("The session started 1 min 35 s after its planned time.");
    expect(rows[2]).toMatchObject({ value: "Partial", tone: "fair" });
  });

  it("says what is missing without a transcript", () => {
    const [transcript, clock] = methodRows(crowd);
    expect(transcript).toMatchObject({ value: "None yet", details: ["Fetch the transcript to read speech against the chat."] });
    expect(clock.value).toBe("Chat only");
    expect(clock.details).toContain("Why: there is no transcript.");
  });
});

describe("the rows on their own", () => {
  it("words a degraded transcript's reasons and a language that doesn't match", () => {
    const row = transcriptRow({
      state: "degraded",
      reasons: ["little_speech", "language_mismatch", "something_new"],
      words: {},
      coverage: { shareOfAudio: 0.4 },
      speakers: {},
      language: { detected: "fr", expected: "en", match: false },
    });
    expect(row).toMatchObject({ value: "Usable, with care", tone: "fair" });
    expect(row.details).toEqual([
      "Speech covers 40% of the recording.",
      "Spoken: French; the event is set to English.",
      "Speech covers little of the recording.",
      "The spoken language isn't the event's.",
    ]);
  });

  it("words an assumed and a misaligned clock with their reasons", () => {
    expect(clockRow({ alignment: { status: "assumed", reasons: ["few_anchors"] } })).toMatchObject({
      value: "Assumed",
      tone: "fair",
      details: [
        "The recording is taken to start with the session: times hold to the minute, not the second.",
        "Why: fewer than two moments agree.",
      ],
    });
    const misaligned = clockRow({ alignment: { status: "misaligned", reasons: ["duration_gap", "drift"] } });
    expect(misaligned.value).toBe("Doesn't line up");
    expect(misaligned.details[1]).toBe("Why: the recording and the session differ in length; the recording drifts against the chat.");
  });

  it("words a complete people list read from Livestorm, and a sync in progress", () => {
    const row = peopleRow({ source: "paginated", coverage: "complete", peopleCount: 612, recordCount: 612, sync: { active: true } });
    expect(row).toMatchObject({ value: "Complete", tone: "good" });
    expect(row.details).toEqual(["612 of the 612 people Livestorm counts.", "Reading the full list from Livestorm now."]);
    const partial = peopleRow(
      { source: "paginated", coverage: "partial", peopleCount: 590, recordCount: 612, sync: {} },
      { state: "partial", value: 0.964 },
    );
    expect(partial.details).toEqual(["590 of the 612 people Livestorm counts.", "Coverage: 96%."]);
  });

  it("says who wrote is unknown when the authors are unresolved, and leaves out a session with nobody", () => {
    expect(authorsRow({ state: "unresolved" })).toMatchObject({ value: "Unknown", tone: "low" });
    expect(authorsRow({ state: "insufficient" })).toBeNull();
    expect(authorsRow(undefined)).toBeNull();
    expect(authorsRow({ state: "ok", value: 0.9, num: 9, den: 10 })).toMatchObject({ value: "90% matched", tone: "good" });
  });

  it("words the chat sorting's three states", () => {
    expect(labelsRow({ state: "ready", messages: 600, labelledAt: "2026-11-10T12:00:00+00:00" }).details[0]).toBe(
      "600 messages sorted on 10 Nov 2026.",
    );
    expect(labelsRow({ state: "stale" }).value).toBe("Out of date");
    expect(labelsRow({ state: "none" }).value).toBe("Not yet");
  });
});

describe("notShown", () => {
  it("lists every hidden number in page order with the server's reason", () => {
    const hidden = notShown(small.record.measures);
    expect(hidden.map((row) => row.id)).toEqual([
      "m.wrote_rate",
      "m.time_to_answer",
      "m.questions_total",
      "m.chat_themes",
      "m.room_map",
      "m.chat_concentration",
      "m.channel_quality",
      "m.segment_quality",
      "m.lead_time_show_up",
    ]);
    const why = Object.fromEntries(hidden.map((row) => [row.id, row.why]));
    const wrote = small.record.measures["m.wrote_rate"];
    expect(why["m.wrote_rate"]).toBe(`${wrote.gate.actual} attendees; shown from ${wrote.gate.minimum}.`);
    expect(why["m.questions_total"]).toBe("Run the analysis to sort the chat.");
    expect(why["m.channel_quality"]).toBe("No channels; shown from 2.");
  });

  it("is short when most numbers show", () => {
    expect(notShown(crowd.record.measures).map((row) => row.id)).toEqual(["m.time_to_answer", "m.questions_total", "m.chat_themes"]);
  });

  it("words each kind of gate and state", () => {
    expect(gateReason({ field: "sourced_share", minimum: 0.5, actual: 0.2 }, { sourced_share: { share: "of registrants have a source" } })).toBe(
      "20% of registrants have a source; shown from 50%.",
    );
    expect(gateReason({ field: "session_length_s", minimum: 300, actual: 120 }, { session_length_s: { seconds: true } })).toBe(
      "The session lasted 2 min; shown from 5 min.",
    );
    expect(gateReason({ field: "unknown", minimum: 1, actual: 0 })).toBe("Not enough data for a reliable number.");
    expect(gateReason(null)).toBe("Not enough data for a reliable number.");
    expect(hiddenWhy({ state: "unresolved" })).toBe("Who wrote is unknown for this session.");
    expect(hiddenWhy({ state: "unavailable", coverage: { missing: ["chat"] } })).toBe("The chat wasn't fetched.");
    expect(hiddenWhy({ state: "ok" })).toBe("");
  });
});

describe("methodSummary", () => {
  it("says the transcript's and the clock's state, and how many numbers are hidden", () => {
    expect(methodSummary(small)).toEqual({ takeaway: "Transcript: good, clock verified.", chip: "9 not shown" });
    expect(methodSummary(crowd)).toEqual({ takeaway: "Transcript: none yet.", chip: "3 not shown" });
  });
});
