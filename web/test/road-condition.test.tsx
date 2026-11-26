// The condition of a road or a route place (#4451, page half): the line's wording for each case, the note after a change, the choices, the
// DM-only door in the mock, and the switch, off until the placement is built.
import { describe, expect, it } from "vitest";

import { ApiError } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import { ROAD_CONDITION_LIVE } from "../src/content/road-condition-switch";
import { roadConditionWords as w } from "../src/content/road-condition-words";

describe("a road's or route place's condition (#4451)", () => {
  it("the line says the value, the session it began, and the value before, with a capital first letter", () => {
    expect(w.line("closed", "", 5, { value: "open", words: "" })).toBe("🚧 Condition: Closed since session 5 (was open)");
    expect(w.line("closed", "", null, { value: "open", words: "" })).toBe("🚧 Condition: Closed (was open)");
    expect(w.line("closed", "", null, null)).toBe("🚧 Condition: Closed");
    expect(w.line("hard", "", 2, null)).toBe("🚧 Condition: Hard going since session 2");
  });

  it("an other value shows the DM's own words", () => {
    expect(w.line("other", "half washed out at the ford", null, null)).toBe("🚧 Condition: Half washed out at the ford");
  });

  it("the note after a change says what is now, what it was, and its own words", () => {
    expect(w.noted("Icewind Pass", "closed", "", null)).toBe("🚧 Noted: Icewind Pass is closed.");
    expect(w.noted("Icewind Pass", "closed", "", "open")).toBe("🚧 Noted: Icewind Pass was open; now it's closed.");
    expect(w.noted("Icewind Pass", "other", "half washed out at the ford", null)).toBe("🚧 Noted for Icewind Pass: half washed out at the ford.");
    expect(w.noted("Icewind Pass", "none", "", "closed")).toBe("🚧 Noted: no condition for Icewind Pass.");
  });

  it("the menu offers the six choices in order, with the words as written", () => {
    expect(w.choices.map((c) => c.label)).toEqual(["Open", "Hard going", "Closed", "Gone", "Something else…", "Not sure"]);
    expect(w.choices[5]!.value).toBe("none");
    expect(w.choices[0]!.hint).toBe("Nothing in the way");
  });

  it("the door refuses a player, so the page draws nothing for one", async () => {
    const api = mockApi("table");
    await expect(api.roadCondition!("100.cmp1.web-player", { on: "place", id: "p1", value: "open" })).rejects.toBeInstanceOf(ApiError);
    await expect(api.roadCondition!("100.cmp1.web-player", { on: "place", id: "p1", value: "open" })).rejects.toMatchObject({ kind: "not-allowed" });
  });

  it("the switch is off until the placement is built", () => {
    expect(ROAD_CONDITION_LIVE).toBe(false);
  });
});
