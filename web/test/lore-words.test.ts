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
    expect([LORE_TEXT_MAX, LORE_LINK_MAX]).toEqual([4000, 2000]);
  });
});
