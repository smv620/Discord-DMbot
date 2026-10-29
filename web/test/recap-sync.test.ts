// The rules that keep a recap's voice and music in step (the same numbers as the test review page).
import { describe, expect, it } from "vitest";

import { clock, DRIFT_SECONDS, needsFollow, seekTarget } from "../src/account/recap-sync";

describe("recap sync rules", () => {
  it("moves the follower only past 0.15 seconds of drift", () => {
    expect(DRIFT_SECONDS).toBe(0.15);
    expect(needsFollow(10, 10.1)).toBe(false);
    expect(needsFollow(10, 9.9)).toBe(false);
    expect(needsFollow(10, 10.2)).toBe(true);
    expect(needsFollow(10, 9.7)).toBe(true);
  });

  it("never seeks to the very end", () => {
    expect(seekTarget(100, 100)).toBeCloseTo(99.95);
    expect(seekTarget(42, 100)).toBe(42);
    expect(seekTarget(-5, 100)).toBe(0);
    expect(seekTarget(500, Number.NaN)).toBe(500);
  });

  it("writes the time as minutes and seconds", () => {
    expect(clock(65)).toBe("1:05");
    expect(clock(0)).toBe("0:00");
    expect(clock(Number.NaN)).toBe("0:00");
  });
});
