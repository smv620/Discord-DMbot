// After `npm run build`: open every built page in a real browser and check what the DOM-only
// tests in test/ can't (#1224, WCAG 2.1 AA): colour contrast in light and dark, no sideways
// scrolling at 320 px wide, a visible focus ring on every control reached with Tab, one h1 and no skipped
// heading levels, and no motion when the reader asks for less. axe-core fails the run on serious and critical findings.
//
// Needs Chrome or Chromium: set A11Y_CHROME to its path, or it uses the installed Google Chrome
// (the CI runners have one). Needs no network: the pages are served from dist/ on localhost.
import { readdirSync, readFileSync, statSync, existsSync } from "node:fs";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { join, relative } from "node:path";

import { chromium } from "playwright-core";

const dist = new URL("../dist/", import.meta.url).pathname;
const axeSource = readFileSync(createRequire(import.meta.url).resolve("axe-core/axe.min.js"), "utf8");

function pages(dir) {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    return statSync(full).isDirectory() ? pages(full) : [full];
  });
}

// "/help" is dist/help.html (the site builds with format "file"), "/" is index.html.
function fileFor(url) {
  const path = decodeURIComponent(url.split(/[?#]/)[0]);
  const base = path === "/" ? "/index.html" : path;
  return [join(dist, base), join(dist, `${base}.html`)].find((f) => existsSync(f) && statSync(f).isFile());
}

const server = createServer((req, res) => {
  const file = fileFor(req.url ?? "/");
  if (!file) {
    res.writeHead(404).end("not found");
    return;
  }
  const type = file.endsWith(".html") ? "text/html; charset=utf-8" : file.endsWith(".css") ? "text/css" : file.endsWith(".js") ? "text/javascript" : "application/octet-stream";
  res.writeHead(200, { "content-type": type }).end(readFileSync(file));
});
await new Promise((ok) => server.listen(0, "127.0.0.1", ok));
const origin = `http://127.0.0.1:${server.address().port}`;

const addresses = pages(dist)
  .filter((f) => f.endsWith(".html"))
  .map((f) => "/" + relative(dist, f).replace(/\.html$/, ""))
  .map((p) => (p === "/index" ? "/" : p))
  .sort();

const browser = await chromium.launch(
  process.env.A11Y_CHROME ? { executablePath: process.env.A11Y_CHROME } : { channel: "chrome" },
);

const failures = [];
const fail = (page, what) => failures.push(`${page}: ${what}`);

async function axeFindings(page) {
  await page.evaluate(axeSource);
  return page.evaluate(async () => {
    const result = await window.axe.run(document, {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] },
    });
    return result.violations
      .filter((v) => v.impact === "serious" || v.impact === "critical")
      .map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
  });
}

// Tab through the page: every control it lands on must show where it is.
async function focusRingFindings(page) {
  const bad = [];
  for (let i = 0; i < 60; i++) {
    await page.keyboard.press("Tab");
    const seen = await page.evaluate(() => {
      const el = document.activeElement;
      if (!el || el === document.body) return null;
      const s = getComputedStyle(el);
      const outline = s.outlineStyle !== "none" && parseFloat(s.outlineWidth) > 0;
      const shadow = s.boxShadow !== "none";
      return { name: `${el.tagName.toLowerCase()}${el.id ? "#" + el.id : ""} "${(el.textContent ?? "").trim().slice(0, 30)}"`, ok: outline || shadow };
    });
    if (seen === null) break;
    if (!seen.ok) bad.push(seen.name);
  }
  return bad;
}

for (const address of addresses) {
  for (const scheme of ["light", "dark"]) {
    const context = await browser.newContext({ colorScheme: scheme, viewport: { width: 1280, height: 800 } });
    const page = await context.newPage();
    await page.goto(origin + address, { waitUntil: "load" });
    await page.waitForTimeout(300); // the account page's script draws its first view
    const label = `${address} (${scheme})`;
    for (const finding of await axeFindings(page)) fail(label, finding);
    const h1s = await page.locator("h1").count();
    if (h1s !== 1) fail(label, `${h1s} h1 headings, want 1`);
    const levels = await page.evaluate(() => [...document.querySelectorAll("h1,h2,h3,h4,h5,h6")].map((h) => Number(h.tagName[1])));
    levels.forEach((level, i) => {
      if (i > 0 && level > levels[i - 1] + 1) fail(label, `heading jumps from h${levels[i - 1]} to h${level}`);
    });
    for (const name of await focusRingFindings(page)) fail(label, `no visible focus ring on ${name}`);
    await context.close();
  }

  // 320 px wide is also what 400% zoom on a desktop screen gives.
  const narrow = await browser.newContext({ viewport: { width: 320, height: 640 } });
  const small = await narrow.newPage();
  await small.goto(origin + address, { waitUntil: "load" });
  await small.waitForTimeout(300);
  const width = await small.evaluate(() => document.documentElement.scrollWidth);
  if (width > 320) fail(`${address} (320 px)`, `scrolls sideways: the page is ${width} px wide`);
  await narrow.close();

  const calm = await browser.newContext({ reducedMotion: "reduce", viewport: { width: 1280, height: 800 } });
  const still = await calm.newPage();
  await still.goto(origin + address, { waitUntil: "load" });
  const moving = await still.evaluate(() => {
    const seconds = (value) => Math.max(...value.split(",").map((v) => parseFloat(v) * (v.trim().endsWith("ms") ? 0.001 : 1)));
    return [...document.querySelectorAll("*")]
      .filter((el) => {
        const s = getComputedStyle(el);
        return seconds(s.animationDuration) > 0.05 || seconds(s.transitionDuration) > 0.05 || s.scrollBehavior === "smooth";
      })
      .map((el) => el.tagName.toLowerCase() + (el.className ? "." + String(el.className).split(" ")[0] : ""))
      .slice(0, 5);
  });
  if (moving.length > 0) fail(`${address} (reduced motion)`, `still moves: ${moving.join(", ")}`);
  await calm.close();
}

await browser.close();
server.close();

if (failures.length > 0) {
  console.error(`Accessibility problems:\n${failures.join("\n")}`);
  process.exit(1);
}
console.log(`Accessibility checks passed on ${addresses.length} pages (light, dark, 320 px, reduced motion).`);
