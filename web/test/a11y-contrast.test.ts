// Contrast (#1146, WCAG 2.2 AA 1.4.3 and 1.4.11): the colour pairs the stylesheet uses, measured
// from its own colour tokens, for light and for dark. Text needs 4.5:1, and focus rings and
// control edges need 3:1.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const css = readFileSync(fileURLToPath(new URL("../src/styles/global.css", import.meta.url)), "utf8");

// The first :root block is light; the one inside prefers-color-scheme: dark overrides it.
function tokens(block: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of block.matchAll(/--([a-z-]+):\s*(#[0-9a-fA-F]{6})\b/g)) out[m[1]!] = m[2]!;
  return out;
}
const lightBlock = css.slice(css.indexOf(":root"), css.indexOf("@media (prefers-color-scheme: dark)"));
const darkStart = css.indexOf("@media (prefers-color-scheme: dark)");
const darkBlock = css.slice(darkStart, css.indexOf("}", css.indexOf("}", darkStart) ) + 1);
const light = tokens(lightBlock);
const dark = { ...light, ...tokens(darkBlock) };

function channel(v: number): number {
  const c = v / 255;
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}
function luminance(hex: string): number {
  const n = parseInt(hex.slice(1), 16);
  return 0.2126 * channel(n >> 16) + 0.7152 * channel((n >> 8) & 255) + 0.0722 * channel(n & 255);
}
function ratio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi! + 0.05) / (lo! + 0.05);
}

// [foreground token, background token, least ratio]
const pairs: [string, string, number][] = [
  ["text", "bg", 4.5],
  ["text", "surface", 4.5],
  ["text-muted", "bg", 4.5],
  ["text-muted", "surface", 4.5],
  ["text-muted", "accent-soft", 4.5],
  ["accent", "bg", 4.5], // links
  ["accent", "surface", 4.5],
  ["accent-text", "accent", 4.5], // button words
  ["warn", "bg", 4.5],
  ["warn", "warn-soft", 4.5],
  ["danger", "bg", 4.5],
  ["danger", "surface", 4.5],
  ["focus", "bg", 3],
  ["focus", "surface", 3],
  ["control-edge", "bg", 3], // the edge of a text box
  ["control-edge", "surface", 3],
];

// The quiet buttons (#1782): accent words on a 16% accent tint over the card or the page, the filled
// hover look, and the grey email hint. The tint is blended here as the stylesheet's color-mix does.
function blend(fg: string, bg: string, share: number): string {
  const f = parseInt(fg.slice(1), 16);
  const b = parseInt(bg.slice(1), 16);
  const mix = (shift: number) => Math.round(((f >> shift) & 255) * share + ((b >> shift) & 255) * (1 - share));
  return "#" + [16, 8, 0].map((shift) => mix(shift).toString(16).padStart(2, "0")).join("");
}

describe.each([
  ["light", light],
  ["dark", dark],
])("%s quiet buttons (#1806)", (_name, t) => {
  it.each(["surface", "bg"])("accent words on the tint over %s are at least 4.5:1", (under) => {
    expect(ratio(t.accent!, blend(t.accent!, t[under]!, 0.16))).toBeGreaterThanOrEqual(4.5);
  });
  it("the grey email hint is at least 4.5:1 on the page and on cards", () => {
    expect(ratio(t["text-muted"]!, t.bg!)).toBeGreaterThanOrEqual(4.5);
    expect(ratio(t["text-muted"]!, t.surface!)).toBeGreaterThanOrEqual(4.5);
  });
  it("the quiet secondary button's edge is at least 3:1", () => {
    expect(ratio(t["control-edge"]!, t.surface!)).toBeGreaterThanOrEqual(3);
    expect(ratio(t["control-edge"]!, t.bg!)).toBeGreaterThanOrEqual(3);
  });
});

describe.each([
  ["light", light],
  ["dark", dark],
])("%s colours", (_name, t) => {
  it.each(pairs)("%s on %s is at least %s:1", (fg, bg, least) => {
    expect(t[fg], `no --${fg}`).toBeDefined();
    expect(t[bg], `no --${bg}`).toBeDefined();
    expect(ratio(t[fg]!, t[bg]!)).toBeGreaterThanOrEqual(least);
  });
});
