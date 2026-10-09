// The table artwork behind the pages (global.css): its files must exist and stay small,
// and the CSS must keep it decorative, quiet and off for people who asked for less data.
import { existsSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const root = new URL("../", import.meta.url).pathname;
const css = readFileSync(join(root, "src/styles/global.css"), "utf8");

describe("background artwork", () => {
  const files = [...css.matchAll(/url\("\/(art\/[^"]+)"\)/g)].map((m) => m[1] as string);

  it("uses a wide picture for computers and a tall one for phones", () => {
    expect(files.sort()).toEqual(["art/table-tall.webp", "art/table-wide.webp"]);
  });

  it.each(["art/table-tall.webp", "art/table-wide.webp"])("%s exists and is light (under 120 KB)", (file) => {
    const path = join(root, "public", file);
    expect(existsSync(path)).toBe(true);
    expect(statSync(path).size).toBeLessThan(120 * 1024);
  });

  it("is a fixed, click-through layer behind the page, as wide as the screen", () => {
    const rule = css.match(/body::before \{[^}]*\}/)?.[0] ?? "";
    expect(rule).toContain("position: fixed");
    expect(rule).toContain("pointer-events: none");
    expect(rule).toContain("z-index: -1");
    expect(rule).toContain("width: 100%");
  });

  it("is turned off for reduced data, high contrast, forced colours and print", () => {
    const off = css.match(/@media ([^{]*)\{\s*body::before \{\s*display: none/)?.[1] ?? "";
    for (const query of ["prefers-reduced-data: reduce", "prefers-contrast: more", "forced-colors: active", "print"]) {
      expect(off).toContain(query);
    }
  });

  it("caches the pictures for a day (their names have no hash, so never 'immutable')", () => {
    const headers = readFileSync(join(root, "public/_headers"), "utf8");
    expect(headers).toMatch(/\/art\/\*\s+Cache-Control: public, max-age=86400/);
    expect(headers).not.toMatch(/\/art\/\*\s+Cache-Control:[^\n]*immutable/);
  });

  it("stays quiet: dark pages at most 0.7, light pages a faint haze", () => {
    const tokens = [...css.matchAll(/--art-opacity: ([\d.]+)/g)].map((m) => Number(m[1]));
    expect(tokens).toHaveLength(2);
    const [light, dark] = tokens as [number, number];
    expect(light).toBeLessThanOrEqual(0.2);
    expect(dark).toBeLessThanOrEqual(0.7);
    expect(css.match(/body::before \{[^}]*opacity: var\(--art-opacity\)/)).not.toBeNull();
  });
});
