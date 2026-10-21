// The import words stay hidden until IMPORT_LIVE, and the switch and its What's new entry move
// together (#2107). The limits the page names mirror the importer's (PLAN, "Importing older sessions").
import { describe, expect, it } from "vitest";

import { dmSteps } from "../src/content/help";
import { IMPORT_LIVE, IMPORT_MAX_FILES, IMPORT_MAX_MB, importSteps } from "../src/content/import-words";
import { entries, held } from "../src/content/whats-new";

describe("importing older sessions", () => {
  it("holds the entry and the Help step back exactly while the switch is off", () => {
    expect(held.some((h) => h.title === "Bring in older sessions")).toBe(!IMPORT_LIVE);
    expect(entries.some((e) => e.title === "Bring in older sessions")).toBe(IMPORT_LIVE);
    const shown = dmSteps.map((s) => s.text).join(" ");
    expect(shown.includes("press Import sessions")).toBe(IMPORT_LIVE);
  });

  it("names the limits from the constants and the consent rule in plain words", () => {
    const words = importSteps.map((s) => s.text).join(" ");
    expect(words).toContain(`up to ${IMPORT_MAX_FILES} text files`);
    expect(words).toContain(`up to ${IMPORT_MAX_MB} MB`);
    for (const part of [
      "agreed to be recorded in Discord",
      "For each name in a file",
      "Craig is a Discord recording bot",
      "Your word that everyone agreed is not enough",
      "never stored",
      "can be read by everyone in your server",
      "Nothing becomes a fact without your say",
    ]) {
      expect(words, part).toContain(part);
    }
    expect(words).not.toMatch(/API|JSON|parse|endpoint/i);
  });
});
