// The AI helper's What's new line moves up on the same day as its switch (#1463): held while
// SUPPORT_AI_LIVE is off, shown once it is on, never both and never neither.
import { describe, expect, it } from "vitest";

import { SUPPORT_AI_LIVE } from "../src/content/support-words";
import { entries, held } from "../src/content/whats-new";

describe("the helper's What's new line and its switch", () => {
  const title = "DMbot's helper answers questions";

  it("is held exactly while the helper is off, and shown exactly while it is on", () => {
    expect(held.some((h) => h.title === title)).toBe(!SUPPORT_AI_LIVE);
    expect(entries.some((e) => e.title === title)).toBe(SUPPORT_AI_LIVE);
  });

  it("says what is true: an AI helper, not a person, and a person takes over", () => {
    const text = [...entries, ...held].find((e) => e.title === title)?.what ?? "";
    expect(text).toContain("an AI program and not a person");
    expect(text).toContain("a person on the team takes over");
    expect(text).toContain("your account page shows who answered");
  });
});

describe("the whole-conversation What's new line (#2193)", () => {
  const title = "Your support questions now show the whole conversation";

  it("moves up with the helper's switch: held while it is off, shown once it is on", () => {
    expect(held.some((h) => h.title === title)).toBe(!SUPPORT_AI_LIVE);
    expect(entries.some((e) => e.title === title)).toBe(SUPPORT_AI_LIVE);
  });
});
