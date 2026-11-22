// The first meeting's reminder under a waiting claim (#5000, page half of #4942): its Discord marks become plain parts, bold
// names split out, escapes shown as the character, and nothing turned into HTML. The switch stays off until the lead flips it.
import { describe, expect, it } from "vitest";

import { feelingParts } from "../src/account/starting-feeling";
import { FIRST_FEELING_LIVE } from "../src/content/first-feeling-switch";

describe("the first-feeling reminder", () => {
  it("splits bold names out", () => {
    expect(feelingParts("is unfriendly toward **Mithral Keep** and **Brewner**.")).toEqual([
      { text: "is unfriendly toward ", bold: false },
      { text: "Mithral Keep", bold: true },
      { text: " and ", bold: false },
      { text: "Brewner", bold: true },
      { text: ".", bold: false },
    ]);
  });

  it("an escaped mark shows as the mark, with no backslash", () => {
    expect(feelingParts("a \\*star\\* and a \\\\ slash")).toEqual([{ text: "a *star* and a \\ slash", bold: false }]);
  });

  it("two bold parts in one line stay two bold parts", () => {
    const parts = feelingParts("**A** then **B**");
    expect(parts.filter((p) => p.bold).map((p) => p.text)).toEqual(["A", "B"]);
  });

  it("a string with no marks is one plain part", () => {
    expect(feelingParts("You decide how they feel.")).toEqual([{ text: "You decide how they feel.", bold: false }]);
  });

  it("empty gives nothing", () => {
    expect(feelingParts("")).toEqual([]);
  });

  it("text such as <b> stays text", () => {
    expect(feelingParts("<b>not bold</b>")).toEqual([{ text: "<b>not bold</b>", bold: false }]);
  });

  it("an unclosed ** is kept as it is", () => {
    expect(feelingParts("a ** b")).toEqual([{ text: "a ** b", bold: false }]);
  });

  it("an escaped mark inside bold does not close it", () => {
    expect(feelingParts("**a\\**b**")).toEqual([{ text: "a**b", bold: true }]);
    expect(feelingParts("**a\\**b")).toEqual([{ text: "**a**b", bold: false }]);
  });

  it("a backslash at the end stays, and ** at the end or three stars are kept without empty parts", () => {
    expect(feelingParts("a\\")).toEqual([{ text: "a\\", bold: false }]);
    expect(feelingParts("**")).toEqual([{ text: "**", bold: false }]);
    expect(feelingParts("***")).toEqual([{ text: "***", bold: false }]);
  });

  it("the switch is off", () => {
    expect(FIRST_FEELING_LIVE).toBe(false);
  });
});
