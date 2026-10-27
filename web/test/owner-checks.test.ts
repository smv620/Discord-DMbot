// Every held What's new entry that waits on the owner has a check in docs/OWNER_CHECKS.md, and every
// check still has an entry waiting (#1894). The reasons are in whats-new.ts, the checks in the doc.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { ownerCheckProblems, ownerWaiting, readChecks, unclassified } from "../scripts/ownerchecks.mjs";
import { held } from "../src/content/whats-new";

const doc = readFileSync(fileURLToPath(new URL("../../docs/OWNER_CHECKS.md", import.meta.url)), "utf8");

describe("the owner's checks and the held What's new entries", () => {
  it("agree", () => {
    expect(ownerCheckProblems(held, doc)).toEqual([]);
  });

  it("skips entries that wait on something other than the owner", () => {
    const titles = ownerWaiting(held).map((h) => h.title);
    expect(titles).not.toContain("New names from what was said"); // the story extractor
    expect(titles).not.toContain("Narrated recaps with music"); // the ElevenLabs account
    expect(titles).not.toContain("Play a recap's voice and music");
    expect(titles).toContain("Watch the story");
  });

  it("leaves no held entry unclassified", () => {
    expect(unclassified(held).map((h) => h.title)).toEqual([]);
    const odd = [{ title: "A new kind", reason: "Held until somebody looks at it." }];
    expect(unclassified(odd)).toHaveLength(1);
  });

  it("asks for the heading when it is missing", () => {
    expect(() => readChecks("Unlocks: #1")).toThrow(/Not on this list/);
  });

  it("names the entry when it has no check", () => {
    const orphan = [{ title: "A made-up feature", reason: "Held until it has been seen working on the test server (#9999)." }];
    const problems = ownerCheckProblems(orphan, doc);
    expect(problems.some((p) => p.includes('"A made-up feature"') && p.includes("#9999"))).toBe(true);
  });

  it("names the check when its entry is gone", () => {
    const { unlocks } = readChecks(doc);
    expect(unlocks.size).toBeGreaterThan(0);
    const first = [...unlocks][0];
    const problems = ownerCheckProblems([], doc);
    expect(problems.some((p) => p.includes(`#${first}`) && p.includes("went live"))).toBe(true);
  });
});
