// The v1 Report's audience section wording (src/report/audience.js), on the
// synthetic record fixtures: rows in the server's order, labels for the
// folds, formatted cells, and hidden values kept hidden.

import { describe, expect, it } from "vitest";
import small from "./fixtures/record-v1.json";
import crowd from "./fixtures/record-v1-crowd.json";
import {
  audienceSummary,
  channelLabel,
  channelTable,
  concentration,
  formatMinutes,
  leadLabel,
  leadTable,
  roomMap,
  segmentLabel,
  segmentTables,
  sessionShowUp,
} from "../src/report/audience";

const crowdMeasures = crowd.record.measures;
const smallMeasures = small.record.measures;

describe("roomMap", () => {
  it("shows the four cells in order, with shares, when the measure is shown", () => {
    const room = roomMap(crowdMeasures["m.room_map"]);
    expect(room.tiles.map((tile) => tile.key)).toEqual(["stayed_wrote", "stayed_quiet", "left_wrote", "left_quiet"]);
    expect(room.tiles[1]).toMatchObject({ people: "23", share: "26%", emphasis: false, label: "Stayed without writing" });
    expect(room.tiles.filter((tile) => tile.emphasis).map((tile) => tile.key)).toEqual(["left_quiet"]);
    expect(room.sentence).toBe("52 of 90 attendees left before the end without writing.");
    expect(room.status).toBe("estimated");
    expect(room.note).toBe("");
  });

  it("keeps the counts and hides the shares below the gate", () => {
    const room = roomMap(smallMeasures["m.room_map"]);
    expect(room.tiles.map((tile) => tile.people)).toEqual(["3", "0", "2", "0"]);
    expect(room.tiles.every((tile) => tile.share === "")).toBe(true);
    expect(room.sentence).toBe("");
    expect(room.tiles.some((tile) => tile.emphasis)).toBe(false);
    expect(room.note).toMatch(/counts are shown/);
    expect(room.status).toBe("");
  });

  it("hides every count when who wrote is unknown", () => {
    const measure = {
      ...crowdMeasures["m.room_map"],
      state: "unresolved",
      rows: crowdMeasures["m.room_map"].rows.map((row) => ({ ...row, people: null, share: null })),
    };
    const room = roomMap(measure);
    expect(room.tiles.every((tile) => tile.people === "—")).toBe(true);
    expect(room.note).toMatch(/unknown/);
  });
});

describe("formatMinutes", () => {
  it("rounds a table's durations to minutes, keeping seconds under a minute", () => {
    expect(formatMinutes(45)).toBe("45 s");
    expect(formatMinutes(1197)).toBe("20 min");
    expect(formatMinutes(3596)).toBe("1 h 00 min");
    expect(formatMinutes(null)).toBe("—");
  });
});

describe("concentration", () => {
  it("says how few people wrote half the chat", () => {
    const chat = concentration(crowdMeasures["m.chat_concentration"]);
    expect(chat.value).toBe("3 people");
    expect(chat.sentence).toBe("wrote 54% of the 39 audience messages, out of 12 audience members who wrote in the chat.");
  });

  it("gives the message count, not k, below the gate", () => {
    const chat = concentration(smallMeasures["m.chat_concentration"]);
    expect(chat.value).toBe("—");
    expect(chat.sentence).toBe("");
    expect(chat.note).toBe("15 audience messages: too few to say how spread out the chat was.");
  });
});

describe("channels and segments", () => {
  it("labels channels, folds and registration types", () => {
    expect(channelLabel({ key: "newsletter" })).toBe("newsletter");
    expect(channelLabel({ key: "(other)", channels: 5 })).toBe("Other (5 channels)");
    expect(channelLabel({ key: "direct_or_unknown" })).toBe("No source");
    expect(channelLabel({ key: "type:import" })).toBe("Registration type: import");
    expect(segmentLabel({ kind: "other", values: 1 })).toBe("Other (1 answer)");
    expect(segmentLabel({ kind: "value", label: "1-10", value: "1-10" })).toBe("1-10");
  });

  it("formats the channel rows in the server's order", () => {
    const table = channelTable(crowdMeasures["m.channel_quality"]);
    expect(table.rows.map((row) => row.key)).toEqual(["newsletter", "partner.example", "(other)", "direct_or_unknown"]);
    expect(table.rows[0]).toMatchObject({ registrants: "60", share: "50%", showUp: "83%", wrote: "20%", medianTime: "20 min", fold: false });
    expect(table.rows[3].fold).toBe(true);
    expect(table.notes).toEqual([]);
    expect(table.status).toBe("estimated");
  });

  it("shows the counts with empty rates when the channels can't be compared", () => {
    const table = channelTable(smallMeasures["m.channel_quality"]);
    expect(table.rows.every((row) => row.showUp === "—" && row.gated)).toBe(true);
    expect(table.notes[0]).toBe("Too few to compare channels: 4 of 7 registrants have a source. The counts are shown.");
  });

  it("says why there is no channel table", () => {
    const none = { ...crowdMeasures["m.channel_quality"], state: "insufficient", rows: [], coverage: { withSource: 0, registrants: 40 } };
    expect(channelTable(none).empty).toMatch(/No registrant has a source/);
  });

  it("empties the wrote column when who wrote is unknown", () => {
    const measure = crowdMeasures["m.channel_quality"];
    const unknown = {
      ...measure,
      coverage: { ...measure.coverage, chatAuthors: "unresolved" },
      rows: measure.rows.map((row) => ({ ...row, wrote: null, wroteRate: null })),
    };
    const table = channelTable(unknown);
    expect(table.rows.every((row) => row.wrote === "—")).toBe(true);
    expect(table.notes).toContain("Who wrote is unknown for this session, so that column is empty.");
  });

  it("makes one table per form field, named from the coverage", () => {
    const segments = segmentTables(crowdMeasures["m.segment_quality"]);
    expect(segments.tables).toHaveLength(1);
    expect(segments.tables[0]).toMatchObject({ field: "team_size", name: "Team size", detail: "120 answered." });
    expect(segments.tables[0].rows.map((row) => row.label)).toEqual(["1-10", "11-50", "201-1000", "51-200", "Other (1 answer)"]);
    expect(segments.tables[0].rows[4]).toMatchObject({ showUp: "—", gated: true, fold: true });
    expect(segments.tables[0].notes).toEqual(["— Too few people in this row for a rate."]);
  });

  it("says why there is no segment table", () => {
    const none = { ...crowdMeasures["m.segment_quality"], state: "insufficient", rows: [], coverage: { fields: [] } };
    expect(segmentTables(none).empty).toMatch(/no question with set answers/);
  });
});

describe("lead time", () => {
  it("labels single and merged buckets", () => {
    expect(leadLabel({ key: "same_day", buckets: ["same_day"] })).toBe("Under 24 hours");
    expect(leadLabel({ key: "4-7..8-14", buckets: ["4-7", "8-14"] })).toBe("4–14 days");
    expect(leadLabel({ key: "same_day..1-3", buckets: ["same_day", "1-3"] })).toBe("Under 4 days");
    expect(leadLabel({ key: "8-14..15+", buckets: ["8-14", "15+"] })).toBe("8 days or more");
    expect(leadLabel({ key: "same_day..15+", buckets: ["same_day", "1-3", "4-7", "8-14", "15+"] })).toBe("Any time before");
    expect(leadLabel({ key: "after_start", buckets: ["after_start"] })).toBe("After the start");
  });

  it("formats the rows and hides a small row's rate", () => {
    const table = leadTable(crowdMeasures["m.lead_time_show_up"]);
    expect(table.rows.map((row) => row.label)).toEqual(["Under 24 hours", "1–3 days", "4–14 days", "15 days or more", "After the start"]);
    expect(table.rows[0]).toMatchObject({ registrants: "36", share: "30%", attendees: "30", showUp: "83%" });
    expect(table.rows[4]).toMatchObject({ showUp: "—", gated: true });
    expect(table.notes).toEqual(["— Too few people in this row for a rate."]);
  });

  it("keeps the counts when the whole measure is under its gate", () => {
    const table = leadTable(smallMeasures["m.lead_time_show_up"]);
    expect(table.rows.map((row) => row.registrants)).toEqual(["6", "1"]);
    expect(table.notes[0]).toMatch(/counts are shown/);
    expect(table.status).toBe("");
  });
});

describe("the section header", () => {
  it("leads with the room map and the chat concentration", () => {
    expect(audienceSummary(crowdMeasures)).toEqual({
      takeaway: "52 of 90 attendees left before the end without writing.",
      chip: "Half the chat: 3 people",
    });
    expect(audienceSummary(smallMeasures).chip).toBe("");
    expect(sessionShowUp(crowdMeasures)).toBe("75%");
  });

  it("never fails on a record without these measures", () => {
    expect(roomMap(undefined)).toBeNull();
    expect(channelTable(undefined)).toBeNull();
    expect(audienceSummary({}).takeaway).toMatch(/Who stayed/);
  });
});
