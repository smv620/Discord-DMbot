// Page changes (#1289): the dust and rune effect is on every page except the legal ones, off
// under "less motion", CSS only (no script added), and small.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { beforeAll, describe, expect, it } from "vitest";

import Help from "../src/pages/help.astro";
import Home from "../src/pages/index.astro";
import Privacy from "../src/pages/legal/privacy.astro";
import Refunds from "../src/pages/legal/refunds.astro";
import Terms from "../src/pages/legal/terms.astro";

const css = readFileSync(new URL("../src/styles/transitions.css", import.meta.url), "utf8");
const html = new Map<string, string>();

beforeAll(async () => {
  const container = await AstroContainer.create();
  const pages = { home: Home, help: Help, terms: Terms, privacy: Privacy, refunds: Refunds };
  for (const [name, page] of Object.entries(pages)) {
    html.set(name, await container.renderToString(page, { request: new Request(`https://dmbot.example/${name}`) }));
  }
});

describe("page changes", () => {
  it.each(["home", "help"])("%s opts in, only when motion is welcome", (name) => {
    const page = html.get(name) ?? "";
    expect(page).toContain("@media (prefers-reduced-motion:no-preference){@view-transition{navigation:auto}}");
    expect(page).not.toContain("navigation:none");
  });

  it.each(["terms", "privacy", "refunds"])("the legal page %s is plain, in both directions", (name) => {
    const page = html.get(name) ?? "";
    expect(page).toContain("@view-transition{navigation:none}");
    expect(page).toContain("h1{view-transition-name:none}");
    expect(page).not.toContain("navigation:auto");
  });

  it("the banner is every page's h1, and only that", () => {
    expect(css).toMatch(/h1\s*\{\s*view-transition-name:\s*banner/);
    expect(css.match(/view-transition-name/g)).toHaveLength(1);
  });

  it("covers every view-transition pseudo-element in the less-motion block", () => {
    const block = css.slice(css.indexOf("@media (prefers-reduced-motion: reduce)"));
    for (const part of ["group", "image-pair", "old", "new"]) {
      expect(block).toContain(`::view-transition-${part}(*)`);
    }
    expect(block).toContain("animation: none !important");
  });

  it("is CSS only: no script, no router, and the runes are a picture, not text", () => {
    for (const page of html.values()) expect(page).not.toMatch(/ClientRouter|astro:transitions/);
    expect(css).not.toContain("content:"); // no generated text a screen reader could read
    expect(css).toContain("data:image/svg+xml");
  });

  it("never catches a tap: the animation layer lets clicks through (#1289)", () => {
    expect(css).toMatch(/::view-transition\s*\{\s*pointer-events:\s*none/);
  });

  it("starts whole: the first layer's dots, at their start size, cover their whole 37 x 41 tile", () => {
    // The dot is centred at 20% / 30% of its tile: the far corner is about 41 px away.
    const start = Number(/initial-value:\s*(\d+)px/.exec(css)?.[1]);
    expect(start).toBeGreaterThanOrEqual(Math.hypot(0.8 * 37, 0.7 * 41));
    expect(css).toContain(`--dust: ${start}px`);
  });

  it("does not flash: no brightness change, and the banner does not slide", () => {
    expect(css).not.toContain("brightness");
    expect(css).toMatch(/::view-transition-group\(banner\)\s*\{\s*animation:\s*none/);
  });

  it("lasts about 700 ms", () => {
    const times = [...css.matchAll(/(\d+)ms/g)].map((m) => Number(m[1]));
    expect(Math.max(...times)).toBe(700); // owner, 2026-10-15: twice 350, so the runes can be seen
  });

  it("adds well under the 15 KB budget, with no new asset files", () => {
    expect(Buffer.byteLength(css)).toBeLessThan(8 * 1024);
    const publicDir = fileURLToPath(new URL("../public/", import.meta.url));
    const files = (dir: string): string[] =>
      readdirSync(dir).flatMap((n) => (statSync(join(dir, n)).isDirectory() ? files(join(dir, n)) : [join(dir, n)]));
    expect(files(publicDir).filter((f) => /rune|dust/i.test(f))).toEqual([]);
  });

  it("every page has exactly one h1: two elements named banner would drop the whole transition", () => {
    // Read from the source, because the islands can't be rendered outside a real build.
    const pagesDir = fileURLToPath(new URL("../src/pages/", import.meta.url));
    const files = (dir: string): string[] =>
      readdirSync(dir).flatMap((n) => (statSync(join(dir, n)).isDirectory() ? files(join(dir, n)) : [join(dir, n)]));
    const pages = files(pagesDir).filter((f) => f.endsWith(".astro"));
    expect(pages.length).toBeGreaterThan(8);
    for (const file of pages) {
      const source = readFileSync(file, "utf8");
      const own = source.match(/<h1[\s>]/g)?.length ?? 0;
      const fromLegalPage = source.includes("<LegalPage") ? 1 : 0; // LegalPage.astro has the h1
      expect(own + fromLegalPage, file).toBe(1);
    }
  });
});
