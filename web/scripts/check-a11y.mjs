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
import { join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright-core";

const dist = fileURLToPath(new URL("../dist/", import.meta.url));
const axeSource = readFileSync(createRequire(import.meta.url).resolve("axe-core/axe.min.js"), "utf8");

function pages(dir) {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    return statSync(full).isDirectory() ? pages(full) : [full];
  });
}

// "/help" is dist/help.html (the site builds with format "file"), "/" is index.html.
function fileFor(url) {
  let path;
  try {
    path = decodeURIComponent(url.split(/[?#]/)[0]);
  } catch {
    return undefined;
  }
  const base = path === "/" ? "/index.html" : path;
  // Never serve anything outside dist/, whatever the address says.
  return [resolve(dist, "." + base), resolve(dist, "." + `${base}.html`)].find(
    (f) => f.startsWith(dist) && existsSync(f) && statSync(f).isFile(),
  );
}

const server = createServer((req, res) => {
  const file = fileFor(req.url ?? "/");
  if (!file) {
    res.writeHead(404).end("not found");
    return;
  }
  const types = { ".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml", ".png": "image/png", ".webp": "image/webp", ".json": "application/json", ".woff2": "font/woff2", ".txt": "text/plain" };
  const type = types[file.slice(file.lastIndexOf("."))] ?? "application/octet-stream";
  res.writeHead(200, { "content-type": type }).end(readFileSync(file));
});
await new Promise((ok) => server.listen(0, "127.0.0.1", ok));
const origin = `http://127.0.0.1:${server.address().port}`;

const addresses = pages(dist)
  .filter((f) => f.endsWith(".html"))
  .map((f) => "/" + relative(dist, f).replace(/\.html$/, ""))
  .map((p) => (p === "/index" ? "/" : p))
  .sort();

if (addresses.length === 0) {
  console.error("No built pages in dist/. Run `npm run build` first.");
  process.exit(1);
}

let browser;
try {
  browser = await chromium.launch(
    process.env.A11Y_CHROME ? { executablePath: process.env.A11Y_CHROME } : { channel: "chrome" },
  );
} catch (error) {
  console.error(`Could not start Chrome (${error.message.split("\n")[0]}). Install Google Chrome, or set A11Y_CHROME to a Chrome or Chromium binary.`);
  process.exit(1);
}

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

// Tab through the page: every control it lands on must show where it is. A drop shadow that is
// there all the time doesn't count, so the shadow is compared with the control's unfocused one.
async function focusRingFindings(page) {
  const bad = [];
  const first = new Set();
  let stops = 0;
  for (let i = 0; i < 80; i++) {
    await page.keyboard.press("Tab");
    const seen = await page.evaluate(() => {
      const el = document.activeElement;
      if (!el || el === document.body) return null;
      const name = `${el.tagName.toLowerCase()}${el.id ? "#" + el.id : ""} "${(el.textContent ?? "").trim().slice(0, 30)}"`;
      const s = getComputedStyle(el);
      const outline = s.outlineStyle !== "none" && parseFloat(s.outlineWidth) > 0 && s.outlineColor !== "rgba(0, 0, 0, 0)";
      const focusedShadow = s.boxShadow;
      el.blur();
      const shadow = focusedShadow !== "none" && focusedShadow !== getComputedStyle(el).boxShadow;
      el.focus();
      return { name, ok: outline || shadow };
    });
    if (seen === null) continue;
    if (first.has(seen.name)) break; // Tab came round to the start
    first.add(seen.name);
    stops++;
    if (!seen.ok) bad.push(seen.name);
  }
  if (stops === 0) bad.push("(Tab reached no control at all)");
  return bad;
}

async function open(browser, address, options) {
  const context = await browser.newContext(options);
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  await page.goto(origin + address, { waitUntil: "load" });
  await page.waitForSelector("h1"); // the account page draws its first view from a script
  return { context, page };
}

async function checkPage(address) {
  for (const scheme of ["light", "dark"]) {
    const { context, page } = await open(browser, address, { colorScheme: scheme, viewport: { width: 1280, height: 800 } });
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

  // 320 px wide is also what 400% zoom on a desktop screen gives. A page that hides its own
  // overflow would pass the scroll width, so what sticks out of the window is measured too.
  {
    const { context, page } = await open(browser, address, { viewport: { width: 320, height: 640 } });
    const wide = await page.evaluate(() => {
      // A row that scrolls inside its own box (the pricing cards on a phone) is allowed to be
      // wider than the window: axe's scrollable-region-focusable rule makes sure the keyboard
      // can scroll it.
      const inOwnScroller = (el) => {
        for (let up = el.parentElement; up && up !== document.body; up = up.parentElement) {
          if (/auto|scroll/.test(getComputedStyle(up).overflowX)) return true;
        }
        return false;
      };
      const out = [...document.querySelectorAll("body *")]
        .filter((el) => el.getBoundingClientRect().right > 321 && getComputedStyle(el).position !== "fixed" && !inOwnScroller(el))
        .map((el) => el.tagName.toLowerCase() + (el.className ? "." + String(el.className).split(" ")[0] : ""));
      return { scroll: document.documentElement.scrollWidth, out: [...new Set(out)].slice(0, 5) };
    });
    if (wide.scroll > 320) fail(`${address} (320 px)`, `scrolls sideways: the page is ${wide.scroll} px wide`);
    else if (wide.out.length > 0) fail(`${address} (320 px)`, `sticks out past the window: ${wide.out.join(", ")}`);
    await context.close();
  }

  {
    const { context, page } = await open(browser, address, { reducedMotion: "reduce", viewport: { width: 1280, height: 800 } });
    const moving = await page.evaluate(() => {
      const seconds = (value) => Math.max(0, ...value.split(",").map((v) => (parseFloat(v) || 0) * (v.trim().endsWith("ms") ? 0.001 : 1)));
      return [...document.querySelectorAll("*")]
        .filter((el) => {
          const s = getComputedStyle(el);
          return seconds(s.animationDuration) > 0.05 || seconds(s.transitionDuration) > 0.05 || s.scrollBehavior === "smooth";
        })
        .map((el) => el.tagName.toLowerCase() + (el.className ? "." + String(el.className).split(" ")[0] : ""))
        .slice(0, 5);
    });
    if (moving.length > 0) fail(`${address} (reduced motion)`, `still moves: ${moving.join(", ")}`);
    await context.close();
  }
}

try {
  for (const address of addresses) {
    try {
      await checkPage(address);
    } catch (error) {
      // One page that won't load must not hide what the others show.
      fail(address, `the check itself failed: ${error.message.split("\n")[0]}`);
    }
  }
} finally {
  await browser.close();
  server.close();
}

if (failures.length > 0) {
  console.error(`Accessibility problems:\n${failures.join("\n")}`);
  process.exit(1);
}
console.log(`Accessibility checks passed on ${addresses.length} pages (light, dark, 320 px, reduced motion).`);
