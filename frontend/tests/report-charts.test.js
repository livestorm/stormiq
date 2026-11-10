// The v1 Report's time-chart geometry (src/report/chartGeometry.js), on the
// synthetic record fixture and on edge cases.

import { describe, expect, it } from "vitest";
import record from "./fixtures/record-v1.json";
import { busiestMinute, clockLabel, minuteTicks, ridgePaths } from "../src/report/chartGeometry";

const minutes = record.record.pulse.minutes;

describe("clockLabel", () => {
  it("writes the session clock", () => {
    expect(clockLabel(0)).toBe("0:00");
    expect(clockLabel(1420)).toBe("23:40");
    expect(clockLabel(3900)).toBe("1:05:00");
    expect(clockLabel(-90)).toBe("−1:30");
    expect(clockLabel(null)).toBe("");
    expect(clockLabel(Number.NaN)).toBe("");
  });
});

describe("minuteTicks", () => {
  it("ticks every 5 minutes on a short session, from 0, placed in percent", () => {
    const ticks = minuteTicks(45);
    expect(ticks.map((tick) => tick.label)).toEqual(["0:00", "5:00", "10:00", "15:00", "20:00", "25:00", "30:00", "35:00", "40:00", "45:00"]);
    expect(ticks[0].left).toBe(0);
    expect(ticks.at(-1).left).toBe(100);
  });

  it("widens the step on a long session to keep the axis readable", () => {
    expect(minuteTicks(120).map((tick) => tick.minute)).toEqual([0, 15, 30, 45, 60, 75, 90, 105, 120]);
    expect(minuteTicks(300).length).toBeLessThanOrEqual(10);
    expect(minuteTicks(0)).toEqual([]);
    expect(minuteTicks(2.5)).toEqual([]);
  });
});

describe("ridgePaths", () => {
  it("draws one point per minute, the busiest at the top", () => {
    const { line, area } = ridgePaths(minutes.map((row) => row.audienceChat));
    const points = line.slice(1).split(" L").map((pair) => pair.split(",").map(Number));
    expect(points).toHaveLength(minutes.length);
    expect(points[0][0]).toBe(0);
    expect(points.at(-1)[0]).toBe(1000);
    expect(Math.min(...points.map(([, y]) => y))).toBe(6);
    expect(area.startsWith(line)).toBe(true);
    expect(area.endsWith("Z")).toBe(true);
  });

  it("draws a flat line when nobody wrote, and nothing without minutes", () => {
    const { line } = ridgePaths([0, 0, 0]);
    expect(new Set(line.slice(1).split(" L").map((pair) => pair.split(",")[1]))).toEqual(new Set(["99.0"]));
    expect(ridgePaths([])).toEqual({ line: "", area: "" });
    expect(ridgePaths([4]).line).toBe("M500.0,6.0");
  });

  it("treats a missing or negative value as 0", () => {
    expect(ridgePaths([null, -2, 3]).line).toBe(ridgePaths([0, 0, 3]).line);
  });
});

describe("busiestMinute", () => {
  it("finds the earliest busiest minute of a lane", () => {
    const peak = busiestMinute(minutes, "audienceChat");
    const most = Math.max(...minutes.map((row) => row.audienceChat));
    expect(peak.count).toBe(most);
    expect(peak.minute).toBe(minutes.find((row) => row.audienceChat === most).minute);
    expect(busiestMinute([{ minute: 0, audienceChat: 2 }, { minute: 1, audienceChat: 2 }], "audienceChat")).toEqual({ minute: 0, count: 2 });
    expect(busiestMinute([{ minute: 0, audienceChat: 0 }], "audienceChat")).toBeNull();
  });
});
