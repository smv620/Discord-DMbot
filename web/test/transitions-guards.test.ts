// The guards for the page changes (#1311): size budget, less motion, plain legal pages. The
// checks run on a finished build (scripts/check-transitions.mjs, a CI step after `npm run
// build`), so here they are tried on small pretend builds, good and bad.
import { mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it } from "vitest";

import {
  BUDGET_BYTES,
  legalProblems,
  reducedMotionProblems,
  sizeProblems,
  topLevelRules,
  transitionBytes,
  transitionProblems,
} from "../scripts/transitions.mjs";

const OPT_IN = "@media (prefers-reduced-motion:no-preference){@view-transition{navigation:auto}}";
const PLAIN = "@view-transition{navigation:none}h1{view-transition-name:none}";
const CSS =
  "@property --dust{syntax:'<length>';inherits:false;initial-value:48px}" +
  "h1{view-transition-name:banner}" +
  "::view-transition-old(root){mask-image:url(\"data:image/svg+xml,<svg/>\");animation:dust 350ms}" +
  "@media (prefers-reduced-motion:reduce){::view-transition-group(*){animation:none!important}}" +
  "body{color:red}";

type Build = { css?: string; pages?: Record<string, string>; legal?: Record<string, string> };

const made: string[] = [];
function build({ css = CSS, pages = {}, legal = {} }: Build = {}): string {
  const dist = mkdtempSync(join(tmpdir(), "dmbot-dist-"));
  made.push(dist);
  mkdirSync(join(dist, "_astro"));
  mkdirSync(join(dist, "legal"));
  writeFileSync(join(dist, "_astro", "Base.css"), css);
  for (const [name, style] of Object.entries({ "index.html": OPT_IN, ...pages })) {
    writeFileSync(join(dist, name), `<head><style>${style}</style></head>`);
  }
  for (const [name, style] of Object.entries({ "terms.html": PLAIN, "privacy.html": PLAIN, ...legal })) {
    writeFileSync(join(dist, "legal", name), `<head><style>${style}</style></head>`);
  }
  return dist;
}
afterEach(() => {
  for (const dist of made.splice(0)) rmSync(dist, { recursive: true });
});

describe("a good build", () => {
  it("passes every guard", () => {
    expect(transitionProblems(build())).toEqual([]);
  });
});

describe("the size budget", () => {
  it("counts the rules about the page change, with their inline pictures, and nothing else", () => {
    const rules = topLevelRules(CSS);
    expect(rules).toHaveLength(5);
    const counted = rules.filter((r) => /view-transition|@property --dust/.test(r)).join("");
    expect(transitionBytes(CSS)).toBe(Buffer.byteLength(counted));
    expect(transitionBytes(CSS)).toBeGreaterThan(Buffer.byteLength(CSS) - 30); // body{} is the only rest
    expect(transitionBytes("body{color:red}")).toBe(0);
  });

  it("fails when the page-change CSS is over 15 KB, picture included", () => {
    const picture = "A".repeat(BUDGET_BYTES);
    const css = `${CSS}::view-transition-new(banner){background:url("data:image/svg+xml,${picture}")}`;
    expect(sizeProblems(build({ css }))).toEqual([expect.stringContaining("budget is 15360")]);
  });

  it("is happy exactly at the limit, and fails one byte over", () => {
    const rule = (pad: string): string => `::view-transition-new(banner){--pad:${pad}}`;
    const room = BUDGET_BYTES - transitionBytes(CSS + rule(""));
    expect(sizeProblems(build({ css: CSS + rule("a".repeat(room)) }))).toEqual([]);
    expect(sizeProblems(build({ css: CSS + rule("a".repeat(room + 1)) }))).toHaveLength(1);
  });

  it("fails with nothing found (a broken effect or a broken check)", () => {
    expect(sizeProblems(build({ css: "body{color:red}" }))).toEqual([expect.stringContaining("none found")]);
  });
});

describe("less motion", () => {
  it("fails when a page opts in without the motion condition", () => {
    const dist = build({ pages: { "help.html": "@view-transition{navigation:auto}" } });
    expect(reducedMotionProblems(dist)).toEqual([expect.stringContaining("help.html")]);
  });

  it("fails when the built CSS opts in without the condition", () => {
    const css = `${CSS}@view-transition{navigation:auto}`;
    expect(reducedMotionProblems(build({ css }))).toEqual([expect.stringContaining("_astro")]);
  });

  it("allows the one conditional opt-in and the opt-out, and nothing looser", () => {
    expect(reducedMotionProblems(build())).toEqual([]);
    const looser = "@media (prefers-reduced-motion:reduce){@view-transition{navigation:auto}}";
    expect(reducedMotionProblems(build({ pages: { "help.html": looser } }))).toHaveLength(1);
  });
});

describe("the legal pages", () => {
  it("fail when one is not plain, however it went wrong", () => {
    for (const style of [OPT_IN, "", "@view-transition{navigation:none}", `${PLAIN}${OPT_IN}`]) {
      expect(legalProblems(build({ legal: { "refunds.html": style } })), style).not.toEqual([]);
    }
  });

  it("fail when there are no legal pages to check", () => {
    const dist = build();
    rmSync(join(dist, "legal"), { recursive: true });
    mkdirSync(join(dist, "legal"));
    expect(legalProblems(dist)).toEqual([expect.stringContaining("no pages")]);
  });
});

describe("the legal page source", () => {
  const pagesDir = fileURLToPath(new URL("../src/pages/legal/", import.meta.url));

  it("every page under src/pages/legal uses LegalPage, so a new one can't get the dust by accident", () => {
    const pages = readdirSync(pagesDir).filter((f) => f.endsWith(".astro"));
    expect(pages.length).toBeGreaterThanOrEqual(3);
    for (const page of pages) {
      expect(readFileSync(join(pagesDir, page), "utf8"), page).toMatch(/<LegalPage[\s>]/);
    }
  });

  it("LegalPage hands `plain` to the layout", () => {
    const layout = readFileSync(fileURLToPath(new URL("../src/components/LegalPage.astro", import.meta.url)), "utf8");
    expect(layout).toMatch(/<Base\b[^>]*\bplain\b/);
  });
});
