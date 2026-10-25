// The page-weight check (#1947) on made-up build folders: a page over budget fails by name, a picture
// over the limit fails, a stylesheet's alternative pictures count once, fonts are listed.
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { KB, checkBuild, localReferences, onDemandReferences, table, weighPage } from "../scripts/weight.mjs";

const dirs: string[] = [];
afterEach(() => {
  for (const d of dirs.splice(0)) rmSync(d, { recursive: true, force: true });
});

/** A build folder with these files (text or a number of bytes). */
function build(files: Record<string, string | number>): string {
  const dist = mkdtempSync(join(tmpdir(), "weight-"));
  dirs.push(dist);
  for (const [name, body] of Object.entries(files)) {
    mkdirSync(dirname(join(dist, name)), { recursive: true });
    writeFileSync(join(dist, name), typeof body === "number" ? Buffer.alloc(body, 1) : body);
  }
  return dist;
}

const budgets = { htmlKB: 10, totalKB: 40, imageKB: 20 };
const page = (extra = "") => `<html><head><link rel="stylesheet" href="/_astro/a.css">${extra}</head><body>hi</body></html>`;

describe("what a page loads", () => {
  it("finds the local files in the HTML, and leaves other sites and data alone", () => {
    const refs = localReferences(
      `<link rel="stylesheet" href="/a.css"><script src="/b.js"></script><img src="/c.png" srcset="/d.webp 1x, https://x.example/e.png 2x">
       <link rel="icon" href="/f.svg"><link rel="canonical" href="https://getdmbot.com/x"><img src="data:image/png;base64,AAAA">
       <astro-island component-url="/_astro/G.js" renderer-url="/_astro/H.js"></astro-island>`,
      "html",
    );
    expect(refs.sort()).toEqual(["/_astro/G.js", "/_astro/H.js", "/a.css", "/b.js", "/c.png", "/d.webp", "/f.svg"]);
  });

  it("follows what a script imports and what a stylesheet draws", () => {
    const dist = build({
      "p.html": `<script type="module" src="/_astro/a.js"></script>`,
      "_astro/a.js": `import { x } from "./b.js"; export const y = 1;`,
      "_astro/b.js": `export const x = 2;`,
    });
    const r = weighPage(dist, "p.html");
    expect([...r.files.keys()].sort()).toEqual(["_astro/a.js", "_astro/b.js"]);
  });

  it("follows the minified imports a build writes, with no space before the quote", () => {
    expect(
      localReferences(`import{r as e}from"./a.js";import{b}from"./b.js";export{x}from"./c.js";import"./d.js";const f=()=>import("/_astro/e.js")`, "js").sort(),
    ).toEqual(["./a.js", "./b.js", "./c.js", "./d.js"]);
    // (a chunk written as import("…") is loaded on demand, not up front)
    expect(onDemandReferences(`const f=()=>import("/_astro/e.js");import{r as e}from"./a.js"`)).toEqual(["/_astro/e.js"]);
    expect(localReferences(`import x from"https://other.example/a.js"`, "js")).toEqual([]);
    const dist = build({
      "p.html": `<astro-island component-url="/_astro/Island.js"></astro-island>`,
      "_astro/Island.js": `import{r as e}from"./preact.js";export default()=>e`,
      "_astro/preact.js": 11 * KB,
    });
    const r = weighPage(dist, "p.html");
    expect([...r.files.keys()].sort()).toEqual(["_astro/Island.js", "_astro/preact.js"]);
    expect(r.totalBytes).toBeGreaterThan(11 * KB);
  });

  it("does not count a chunk loaded on demand, but limits its own size", () => {
    const dist = build({
      "p.html": `<astro-island component-url="/_astro/Island.js"></astro-island>`,
      "_astro/Island.js": `export default()=>import("./Panel.js")`,
      "_astro/Panel.js": 30 * KB,
    });
    const r = weighPage(dist, "p.html");
    expect([...r.files.keys()]).toEqual(["_astro/Island.js"]);
    expect([...r.onDemand.keys()]).toEqual(["_astro/Panel.js"]);
    const ok = checkBuild(dist, { htmlKB: 52, totalKB: 185, imageKB: 150, onDemandKB: 40 });
    expect(ok.problems).toEqual([]);
    const big = checkBuild(dist, { htmlKB: 52, totalKB: 185, imageKB: 150, onDemandKB: 20 });
    expect(big.problems.join("\n")).toContain("_astro/Panel.js loads on demand");
  });

  it("reads a stylesheet's @import", () => {
    expect(localReferences(`@import "/_astro/other.css"; a{color:red}`, "css")).toEqual(["/_astro/other.css"]);
  });

  it("never counts a file outside the build folder", () => {
    const dist = build({ "site/p.html": `<script src="/../outside.js"></script><script src="../../outside.js"></script>`, "outside.js": 5 * KB });
    const r = weighPage(join(dist, "site"), "p.html");
    expect([...r.files.keys()]).toEqual([]);
  });

  it("counts each file once, however many times it is named", () => {
    const dist = build({ "p.html": `<link rel="stylesheet" href="/a.css"><link rel="stylesheet" href="/a.css">`, "a.css": 5000 });
    expect(weighPage(dist, "p.html").files.size).toBe(1);
  });

  it("counts a stylesheet's background pictures as alternatives, the largest once", () => {
    const dist = build({
      "p.html": `<link rel="stylesheet" href="/s.css">`,
      "s.css": `@media (orientation: portrait) { body { background: url(/tall.webp) } } @media (orientation: landscape) { body { background: url(/wide.webp) } }`,
      "tall.webp": 3000,
      "wide.webp": 8000,
    });
    const r = weighPage(dist, "p.html");
    expect([...r.files.keys()].sort()).toEqual(["s.css", "wide.webp"]);
    expect([...r.alternatives]).toEqual(["tall.webp"]);
  });
});

describe("the budget", () => {
  it("passes a page within both budgets", () => {
    const dist = build({ "index.html": page(), "_astro/a.css": 2 * KB });
    const result = checkBuild(dist, budgets);
    expect(result.problems).toEqual([]);
    expect(table(result.rows, budgets)).toMatch(/index\.html\s+0\.\d\s+2\.\d\s+10 \/ 40/);
  });

  it("fails a page whose HTML is over, naming it", () => {
    const dist = build({ "index.html": page(), "_astro/a.css": 1 * KB, "big.html": page() + "x".repeat(11 * KB) });
    const { problems } = checkBuild(dist, budgets);
    expect(problems).toHaveLength(1);
    expect(problems[0]).toMatch(/^big\.html: the HTML is 11\.\d KB, over its 10 KB budget$/);
  });

  it("fails a page whose files are over, naming it and leaving the others alone", () => {
    const dist = build({
      "light.html": `<p>hi</p>`,
      "heavy.html": `<script src="/s.js"></script>`,
      "s.js": 41 * KB,
    });
    const { problems } = checkBuild(dist, budgets);
    expect(problems).toHaveLength(1);
    expect(problems[0]).toMatch(/^heavy\.html: everything it loads is 41\.\d KB, over its 40 KB budget$/);
  });

  it("fails a picture over the limit, with the pages that use it", () => {
    const dist = build({
      "a.html": `<img src="/big.png">`,
      "b.html": `<meta property="og:image" content="https://getdmbot.com/big.png">`,
      "big.png": 21 * KB,
      "small.png": 5 * KB,
    });
    const { problems, images } = checkBuild(dist, budgets);
    expect(images.map((i) => i.file)).toEqual(["big.png"]);
    expect(problems.join("\n")).toMatch(/big\.png: 21\.0 KB, over the 20 KB limit for a picture \(used by a\.html, b\.html\)/);
  });

  it("lists the fonts a page loads", () => {
    const dist = build({
      "a.html": `<link rel="stylesheet" href="/s.css">`,
      "s.css": `@font-face { src: url(/f/Body.woff2) format("woff2") }`,
      "f/Body.woff2": 3000,
    });
    expect(checkBuild(dist, budgets).fonts).toEqual(["f/Body.woff2"]);
    expect(checkBuild(build({ "a.html": "<p>hi</p>" }), budgets).fonts).toEqual([]);
  });
});
