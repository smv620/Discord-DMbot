// The Story graph's kind words (#2618): NPCs and PCs, never "People", and a real person at the
// table in a group of their own.
import { describe, expect, it } from "vitest";

import { kindWords } from "../src/content/web-words";

describe("kind words", () => {
  it("calls the characters NPCs and PCs", () => {
    expect(kindWords("npc")).toBe("NPCs");
    expect(kindWords("player_character")).toBe("PCs");
  });

  it("puts a real person at the table in its own group", () => {
    expect(kindWords("person")).toBe("At the table");
  });

  it("never says People, god, deity or creature as a kind of its own", () => {
    for (const k of ["npc", "player_character", "person", "place", "group", "item", "event"]) {
      expect(kindWords(k)).not.toMatch(/people|god|deity|creature/i);
    }
  });

  it("shows a kind it does not know as it comes", () => {
    expect(kindWords("starship")).toBe("starship");
  });
});
