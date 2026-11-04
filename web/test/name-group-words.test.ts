// The words for splitting a name that is really a list and for saying it is a group (#3406). The
// refusal sentences are looked up by the bot's word, so a word that is not in the list falls back.
import { describe, expect, it } from "vitest";

import { nameGroupWords as w } from "../src/content/name-group-words";
import { held } from "../src/content/whats-new";

describe("name group words (#3406)", () => {
  it("reason maps each refusal word to its own sentence", () => {
    for (const [code, sentence] of Object.entries(w.errors)) {
      expect(w.reason(code), code).toBe(sentence);
    }
    expect(w.reason("not_a_list")).toBe("That isn't a list of names, so it can't be split. Keep it as one name.");
  });

  it("reason falls back to other for an unknown word, null and undefined", () => {
    expect(w.reason("server")).toBe(w.other);
    expect(w.reason(null)).toBe(w.other);
    expect(w.reason(undefined)).toBe(w.other);
    expect(w.reason("toString")).toBe(w.other);
  });

  it("hint says is for one member and are for several", () => {
    expect(w.hint("the Bat Family", ["Robin"])).toBe("It looks like the group the Bat Family. Robin is already in that group.");
    expect(w.hint("the Bat Family", ["Robin", "Alfred"])).toBe(
      "It looks like the group the Bat Family. Robin, Alfred are already in that group.",
    );
  });

  it("the held What's new entry exists and is held", () => {
    expect(held.map((h) => h.title)).toContain("Split a list of names, or say it is a group");
  });
});
