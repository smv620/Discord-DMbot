// The monthly recap allowance in the plans FAQ and in Help (#1449): said only while recaps are on,
// the narrated number only while narration is, and from one constant each. The real constants for
// today (both off); the switch-on states are tested on the pure function.
import { describe, expect, it } from "vitest";

import { questions } from "../src/content/pricing";
import {
  RECAPS_LIVE,
  RECAPS_PER_MONTH,
  RECAP_AUDIO_PER_MONTH,
  allowanceSentence,
  recapSteps,
} from "../src/content/recap-words";

describe("the allowance sentence", () => {
  it("says nothing while recaps are off", () => {
    expect(allowanceSentence(false, false)).toBe("");
    expect(allowanceSentence(false, true)).toBe("");
  });

  it("names the recaps, and the narrated ones only with narration", () => {
    expect(allowanceSentence(true, false)).toBe("Each campaign gets up to 10 recaps a month.");
    expect(allowanceSentence(true, true)).toBe(
      "Each campaign gets up to 10 recaps a month. Up to 8 of those can be narrated, with a voice and music.",
    );
  });

  it("takes its numbers from the two constants (they mirror the server's defaults)", () => {
    expect([RECAPS_PER_MONTH, RECAP_AUDIO_PER_MONTH]).toEqual([10, 8]);
    expect(allowanceSentence(true, true)).toContain(`${RECAPS_PER_MONTH} recaps`);
    expect(allowanceSentence(true, true)).toContain(`${RECAP_AUDIO_PER_MONTH} of those`);
  });
});

describe("where it shows", () => {
  it("is in the plans FAQ only while recaps are live", () => {
    const answer = questions.find((f) => f.question === "What's different between the plans?")?.answer;
    expect(answer).toBeTruthy();
    expect(/recaps a month/.test(answer ?? "")).toBe(RECAPS_LIVE);
  });

  it("is a Help step with what happens when the month is used, and no narration while it is off", () => {
    const step = recapSteps.map((s) => s.text).find((t) => t.includes("recaps a month"));
    expect(step).toContain("Each campaign gets up to 10 recaps a month.");
    expect(step).toContain("If you use them all, DMbot tells you on the DM screen, and the next recap comes on the 1st of next month.");
    expect(step).not.toMatch(/narrat/i);
  });
});
