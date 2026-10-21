// Accessibility (#1146, WCAG 2.2 AA): axe-core runs on every page's HTML and fails on any problem,
// whatever its impact (serious and critical since #1146, moderate and minor since #1938), and the
// colour pairs the stylesheet uses are measured for contrast in light and dark (a DOM without
// layout can't measure contrast itself). The keyboard walk in a real browser is in
// scripts/check-a11y.mjs; the parts that need no layout are checked here.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { getContainerRenderer } from "@astrojs/preact/container-renderer";
import axe from "axe-core";
import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { loadRenderers } from "astro:container";
import type { AstroComponentFactory } from "astro/runtime/server/index.js";
import { Window } from "happy-dom";

import { parsePage } from "./dom";
import { beforeAll, describe, expect, it } from "vitest";

const found = import.meta.glob<{ default: AstroComponentFactory }>("../src/pages/**/*.astro", {
  eager: true,
});

// Every built page, by its address.
const pagePaths = Object.keys(found).map((file) => {
  const path = file.replace("../src/pages", "").replace(/\.astro$/, "");
  return path === "/index" ? "/" : path;
});

let container: AstroContainer;
beforeAll(async () => {
  container = await AstroContainer.create({
    renderers: await loadRenderers([getContainerRenderer()]),
  });
});

async function axeOn(html: string): Promise<string[]> {
  const window = new Window();
  window.document.write(html);
  // axe reads window, document and Node from the global scope.
  const g = globalThis as Record<string, unknown>;
  const saved = { window: g.window, document: g.document, Node: g.Node };
  g.window = window;
  g.document = window.document;
  g.Node = window.Node;
  try {
    const result = await axe.run(window.document.documentElement as unknown as Element, {
      // happy-dom has no layout, so the contrast rule can't run here (it is measured below, and in
      // a real browser by scripts/check-a11y.mjs). Every other rule runs, at every impact.
      rules: { "color-contrast": { enabled: false } },
    });
    return result.violations
      .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
  } finally {
    Object.assign(g, saved);
  }
}

async function violationsOn(path: string): Promise<string[]> {
  const file = path === "/" ? "/index" : path;
  const html = await container.renderToString(found[`../src/pages${file}.astro`]!.default, {
    request: new Request(`https://dmbot.example${path}`),
  });
  return axeOn(html);
}

describe("structure", () => {
  it.each(pagePaths)("%s has one h1, headings in order and the four landmarks", async (path) => {
    const file = path === "/" ? "/index" : path;
    const html = await container.renderToString(found[`../src/pages${file}.astro`]!.default, {
      request: new Request(`https://dmbot.example${path}`),
    });
    const doc = parsePage(html);
    expect(doc.documentElement.getAttribute("lang")).toBe("en");
    const levels = [...doc.querySelectorAll("h1, h2, h3, h4, h5, h6")].map((h) =>
      Number(h.tagName.slice(1)),
    );
    expect(levels.filter((l) => l === 1)).toHaveLength(1);
    // A heading may go one level deeper than the one before it, never two.
    levels.forEach((level, i) => {
      if (i > 0) expect(level, `heading ${i} after ${levels[i - 1]}`).toBeLessThanOrEqual(levels[i - 1]! + 1);
    });
    // A shared recap (/r) is a bare page for someone who was only sent a link: no menu, no footer.
    for (const landmark of path === "/r" ? ["main"] : ["header", "nav", "main", "footer"]) {
      expect(doc.querySelector(landmark), landmark).not.toBeNull();
    }
    // The first thing a keyboard user reaches skips to the page.
    expect(doc.querySelector("a")?.getAttribute("href")).toBe("#main");
    expect(doc.querySelector("#main")).not.toBeNull();
  });

  it("shows a focus ring and honours reduced motion", () => {
    const css = readFileSync(fileURLToPath(new URL("../src/styles/global.css", import.meta.url)), "utf8");
    expect(css).toMatch(/:focus-visible\s*\{[^}]*outline:\s*3px/);
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)[^]*animation: none/);
  });
});

describe("axe-core finds nothing, at any impact", () => {
  // Proof the check can fail: a page with an unlabelled picture and no language.
  it("catches a missing alt text and a missing language", async () => {
    const found = await axeOn('<html><body><main><img src="a.png"><h1>x</h1></main></body></html>');
    expect(found.join(" ")).toMatch(/image-alt/);
    expect(found.join(" ")).toMatch(/html-has-lang/);
  });

  // Proof the raised bar can fail (#1938): a skipped heading level and text outside every landmark
  // are moderate findings, which the old serious-and-critical filter let through.
  it("catches moderate findings too", async () => {
    const found = await axeOn(
      '<html lang="en"><body><main><h1>x</h1><h4>skipped</h4></main><p>loose text</p></body></html>',
    );
    expect(found.join(" ")).toMatch(/heading-order/);
    expect(found.join(" ")).toMatch(/region/);
  });

  it.each(pagePaths)("%s", async (path) => {
    expect(await violationsOn(path)).toEqual([]);
  });
});

// The keyboard (#1938). Tab order follows the page order when nothing sets a positive tabindex, and
// every control shows a ring when focus is on it because one :focus-visible rule covers them all
// and nothing takes the outline away. The walk with a real Tab key is in scripts/check-a11y.mjs.
describe("keyboard", () => {
  const keyboardPages = ["/", "/pricing", "/help", "/credits"];
  const stylesheet = readFileSync(fileURLToPath(new URL("../src/styles/global.css", import.meta.url)), "utf8");

  it.each(keyboardPages)("%s has no tabindex above 0, and every link and button has a name", async (path) => {
    const file = path === "/" ? "/index" : path;
    const html = await container.renderToString(found[`../src/pages${file}.astro`]!.default, {
      request: new Request(`https://dmbot.example${path}`),
    });
    const doc = parsePage(html);
    for (const el of doc.querySelectorAll("[tabindex]")) {
      expect(Number(el.getAttribute("tabindex")), el.outerHTML.slice(0, 80)).toBeLessThanOrEqual(0);
    }
    const controls = [...doc.querySelectorAll("a[href], button")];
    expect(controls.length).toBeGreaterThan(5);
    for (const el of controls) {
      const name = (el.textContent ?? "").trim() || el.getAttribute("aria-label") || el.querySelector("img")?.getAttribute("alt");
      expect(name, el.outerHTML.slice(0, 80)).toBeTruthy();
    }
  });

  it("one :focus-visible rule covers links, buttons and inputs, and nothing removes the outline", () => {
    // A bare :focus-visible selector applies to every element, so also to a, button and input.
    expect(stylesheet).toMatch(/(^|\n):focus-visible\s*\{[^}]*outline:\s*3px solid/);
    const srcDir = fileURLToPath(new URL("../src", import.meta.url));
    const files = (dir: string): string[] =>
      readdirSync(dir).flatMap((name) => {
        const full = join(dir, name);
        return statSync(full).isDirectory() ? files(full) : /\.(css|astro|tsx|ts)$/.test(name) ? [full] : [];
      });
    for (const file of files(srcDir)) {
      expect(readFileSync(file, "utf8"), file).not.toMatch(/outline:\s*(none|0)\b/);
    }
  });

  it("catches a positive tabindex", () => {
    const doc = parsePage('<html><body><a href="#a" tabindex="2">x</a></body></html>');
    const bad = [...doc.querySelectorAll("[tabindex]")].filter((e) => Number(e.getAttribute("tabindex")) > 0);
    expect(bad).toHaveLength(1);
  });
});
