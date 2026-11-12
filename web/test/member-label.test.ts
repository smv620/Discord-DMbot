// How a member is named where the DM picks a person (#4225): the Discord name with the username after it when they differ, and never a piece
// of an id or "Player 1520", which reads as a character.
import { describe, expect, it } from "vitest";

import { memberLabel } from "../src/account/memberLabel";

describe("memberLabel", () => {
  it("shows the display name, with the username after it when they differ", () => {
    expect(memberLabel("1234567890", "Eric", "eric_92")).toBe("Eric (eric_92)");
    expect(memberLabel("1234567890", "Eric", "ERIC")).toBe("Eric"); // (the same name twice adds nothing)
    expect(memberLabel("1234567890", "Eric", null)).toBe("Eric");
    expect(memberLabel("1234567890", "Eric")).toBe("Eric");
  });

  it("never shows Player and four digits: it says Unknown member and the end of the id", () => {
    expect(memberLabel("111111111111111520", "Player 1520")).toBe("Unknown member (…1520)");
    expect(memberLabel("999", "Player 3 (no Discord)")).toBe("Unknown member (…999)");
    expect(memberLabel("111111111111111520", "Player 1520", "someone")).toBe("Unknown member (…1520)");
  });

  it("leaves a real name that starts with Player alone", () => {
    expect(memberLabel("5", "Player One", "p1")).toBe("Player One (p1)");
  });
});
