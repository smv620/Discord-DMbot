// The Maps page's per-campaign address is served by the one Maps page (#4347), like every other per-campaign page.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("the Maps address", () => {
  it("is rewritten to the Maps page", () => {
    expect(readFileSync("public/_redirects", "utf8")).toContain("/account/maps/*  /account/maps  200");
  });
});
