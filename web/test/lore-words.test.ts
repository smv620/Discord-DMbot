// The Upload lore page stays hidden until LORE_LIVE, and the switch and its What's new entry move together (#3022).
import { describe, expect, it } from "vitest";

import { LORE_LIVE, LORE_LINK_MAX, LORE_TEXT_MAX } from "../src/content/lore-words";
import { entries, held } from "../src/content/whats-new";

describe("Upload lore", () => {
  it("holds the What's new entry back exactly while the switch is off", () => {
    expect(held.some((h) => h.title === "Upload lore")).toBe(!LORE_LIVE);
    expect(entries.some((e) => e.title === "Upload lore")).toBe(LORE_LIVE);
  });

  it("mirrors the limits of the Discord form and the web door", () => {
    expect([LORE_TEXT_MAX, LORE_LINK_MAX]).toEqual([262_144, 2000]);
  });
});

describe("Upload lore words (#3050)", () => {
  it("says what the smart choice really does and who 'everyone' is", async () => {
    const { lorePage } = await import("../src/content/lore-words");
    expect(lorePage.known.smart.note).toContain("guesses");
    expect(lorePage.known.smart.note).toContain("change any of them later");
    expect(lorePage.known.smart.note).not.toContain("approve every one");
    expect(lorePage.known.world.note).toContain("player's character");
  });

  it("has a word for the sorting stage the bot reports", async () => {
    const { lorePage } = await import("../src/content/lore-words");
    expect(lorePage.stages["sorting"]).toBe("Working out what the players know.");
  });
});
