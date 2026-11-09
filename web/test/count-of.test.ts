// The bot's `counts` text (#3974, #3986): one number per key, said only when it is above 0.
import { describe, expect, it } from "vitest";

import { countOf } from "../src/account/api-names";

describe("countOf", () => {
  it("reads one key from the counts text", () => {
    expect(countOf("shown:1", "shown")).toBe(1);
    expect(countOf("kept:3 shown:2", "shown")).toBe(2);
  });
  it("says nothing for a count of 0, a missing key or a bad number", () => {
    expect(countOf("held:0", "held")).toBeUndefined();
    expect(countOf(undefined, "shown")).toBeUndefined();
    expect(countOf("", "shown")).toBeUndefined();
    expect(countOf("shown:x", "shown")).toBeUndefined();
  });
});
