// A kept picture's link getdmbot.com/pictures/<code> is forwarded to the API (#3274).
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("the picture link", () => {
  it("is forwarded to the API with the code, as a redirect and not a rewrite", () => {
    const redirects = readFileSync("public/_redirects", "utf8");
    expect(redirects).toContain("/pictures/*  https://api.getdmbot.com/pictures/:splat  302");
  });
});
