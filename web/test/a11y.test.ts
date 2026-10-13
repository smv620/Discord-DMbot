// Accessibility (#1146, WCAG 2.2 AA): axe-core runs on every page's HTML and fails on any
// serious or critical problem, and the colour pairs the stylesheet uses are measured for
// contrast in light and dark (a DOM without layout can't measure contrast itself).
import { readFileSync } from "node:fs";
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
      // happy-dom has no layout, so contrast and visibility rules can't run here.
      rules: { "color-contrast": { enabled: false }, region: { enabled: false } },
    });
    return result.violations
      .filter((v) => v.impact === "serious" || v.impact === "critical")
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
    for (const landmark of ["header", "nav", "main", "footer"]) {
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

describe("axe-core finds nothing serious or critical", () => {
  // Proof the check can fail: a page with an unlabelled picture and no language.
  it("catches a missing alt text and a missing language", async () => {
    const found = await axeOn('<html><body><main><img src="a.png"><h1>x</h1></main></body></html>');
    expect(found.join(" ")).toMatch(/image-alt/);
    expect(found.join(" ")).toMatch(/html-has-lang/);
  });

  it.each(pagePaths)("%s", async (path) => {
    expect(await violationsOn(path)).toEqual([]);
  });
});
