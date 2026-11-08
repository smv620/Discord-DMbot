// The website's search bar, line 1 (#3855): the best page for what was typed, matched in the browser, and an entry for every
// page of the site.
import { readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { publicPaths } from "../src/content/site";
import { searchIndex } from "../src/content/search-index";
import { bestMatch } from "../src/search/match";

const pick = (q: string): string | undefined => bestMatch(q)?.path;

describe("which page fits", () => {
  it.each([
    ["a summary of episodes", "/account/recaps"],
    ["how much does it cost", "/pricing"],
    ["stop recording me", "/help#player-heading"],
    ["Recaps", "/account/recaps"],
    ["how do I pronounce a name", "/account/names"],
    ["download a backup of my campaign", "/account/campaigns"],
    ["stop the emails", "/help#emails-heading"],
    ["house rules", "/account/house-rules"],
    ["who knows who", "/account/web"],
    ["refund", "/legal/refunds"],
  ])("%s -> %s", (query, path) => expect(pick(query)).toBe(path));

  it("says nothing fits for gibberish, an empty box, or only little words", () => {
    expect(pick("qzxv wplk")).toBeUndefined();
    expect(pick("")).toBeUndefined();
    expect(pick("   ")).toBeUndefined();
    expect(pick("the of a")).toBeUndefined();
  });

  it("is the same whatever the capitals or spaces", () => {
    expect(pick("  HOW   MUCH does it COST ")).toBe("/pricing");
  });
});

describe("the index", () => {
  it("has an entry for every page in the sitemap", () => {
    const have = new Set(searchIndex.map((e) => e.path.split("#")[0]));
    for (const path of publicPaths) expect([...have], path).toContain(path);
  });

  it("has an entry for every signed-in page", () => {
    const dir = fileURLToPath(new URL("../src/pages/account", import.meta.url));
    const have = new Set(searchIndex.map((e) => e.path));
    const pages = readdirSync(dir).filter((f) => f.endsWith(".astro")).map((f) => `/account/${f.replace(/\.astro$/, "")}`);
    for (const page of pages) expect([...have], page).toContain(page);
    expect([...have]).toContain("/account");
  });

  it("gives every entry words, a title and a plain line, with no repeated address", () => {
    const seen = new Set<string>();
    for (const e of searchIndex) {
      expect(e.title.length, e.path).toBeGreaterThan(0);
      expect(e.about.length, e.path).toBeGreaterThan(0);
      expect(e.words.length, e.path).toBeGreaterThan(0);
      expect(seen.has(e.path), e.path).toBe(false);
      seen.add(e.path);
    }
  });
});
