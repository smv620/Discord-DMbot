// Help says how long a recap is (#2018): the numbers in the sentence are worked out from core's own
// rule (core/src/dmbot/recaps/draft.py: BASE_WORDS + WORDS_PER_HOUR for each hour, to the nearest 10,
// at most WORDS_CEILING), so a change to the rule there fails here until Help is changed too.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { recapSteps } from "../src/content/recap-words";

const draft = readFileSync(
  new URL("../../core/src/dmbot/recaps/draft.py", import.meta.url),
  "utf8",
);
const constant = (name: string): number =>
  Number(new RegExp(`^${name} = (\\d+)`, "m").exec(draft)?.[1]);

const wordsFor = (hours: number): number =>
  Math.min(
    constant("WORDS_CEILING"),
    Math.round((constant("BASE_WORDS") + constant("WORDS_PER_HOUR") * hours) / 10) * 10,
  );

describe("the Help line on how long a recap is", () => {
  const line = recapSteps.map((s) => s.text).find((t) => t.startsWith("A recap's length"));

  it("is there, in plain words", () => {
    expect(line).toBeTruthy();
    expect(line).toContain("follows your session");
  });

  it("gives the numbers core's rule gives", () => {
    expect(wordsFor(1)).toBe(250);
    expect(wordsFor(3)).toBe(450);
    expect(line).toContain(`about ${wordsFor(1)} words for a one-hour game`);
    expect(line).toContain(`about ${wordsFor(3)} for three hours`);
    // The cap is reached at (ceiling - base) / words-per-hour hours: four and a half, as Help says.
    const capHours = (constant("WORDS_CEILING") - constant("BASE_WORDS")) / constant("WORDS_PER_HOUR");
    expect(wordsFor(capHours)).toBe(constant("WORDS_CEILING"));
    expect(line).toContain(`${capHours === 4.5 ? "four and a half" : "?"} hours or more gets about ${constant("WORDS_CEILING")} words`);
  });
});
