import { describe, expect, it } from "vitest";
import { TRANSCRIPT_STEPS, stepsAt, transcriptSteps } from "../src/report/progress";

describe("the loader's steps", () => {
  it("mark the steps before the stage done, the stage current, the rest to do", () => {
    const steps = transcriptSteps({ stage: "transcribing", message: "Transcribing with Gladia: 40%" });
    expect(steps.map((step) => step.state)).toEqual(["done", "done", "done", "current", "todo", "todo"]);
    expect(steps[3]).toEqual({ label: "Transcribing", state: "current", detail: "Transcribing with Gladia: 40%" });
    expect(steps.filter((step) => step.detail)).toHaveLength(1);
  });

  it("start at the first step when the stage is unknown or missing", () => {
    expect(transcriptSteps(null)[0].state).toBe("current");
    expect(stepsAt(TRANSCRIPT_STEPS, "not-a-stage").map((step) => step.state)[0]).toBe("current");
  });
});
