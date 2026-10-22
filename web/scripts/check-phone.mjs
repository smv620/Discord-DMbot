// Phone-width run over the signed-in pages and /pricing (#2250): load each in a real browser at 360
// and 390 px wide (a touch device), walk it through the states that make it widest, and fail when
//   1. the page scrolls sideways (documentElement.scrollWidth > the window's width),
//   2. something sticks out past the window outside a box that scrolls on its own, or
//   3. a button, link-button, field or choice is smaller than 44 x 44 px (WCAG 2.5.5; the site's
//      own --tap is 48 px) or sits within 8 px of another one (stacked full-width disclosure rows, like the
//      questions on /pricing, are not counted as too close).
// It serves dist-smoke/ (the pretend-API build, with the recap and import switches on: see
// astro.smoke.config.mjs), so it builds that first unless PHONE_SKIP_BUILD=1 (or SMOKE_SKIP_BUILD=1).
// PHONE_SHOTS=<folder> also saves a full-page picture of each state.
//
// Needs Chrome or Chromium like check-a11y.mjs: set A11Y_CHROME to its path, or it uses the
// installed Google Chrome (the CI runners have one). Nothing leaves the machine.
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright-core";

const root = fileURLToPath(new URL("../", import.meta.url));
const dist = resolve(root, "dist-smoke") + "/";
const shots = process.env.PHONE_SHOTS;
if (shots) mkdirSync(shots, { recursive: true });

if (!process.env.PHONE_SKIP_BUILD && !process.env.SMOKE_SKIP_BUILD) {
  const built = spawnSync("npx", ["astro", "build", "--config", "astro.smoke.config.mjs"], {
    cwd: root,
    stdio: "inherit",
    env: { ...process.env, PUBLIC_API_BASE: "mock", CF_PAGES_BRANCH: "" },
  });
  if (built.status !== 0) {
    console.error("The smoke build failed.");
    process.exit(1);
  }
}

if (!existsSync(dist)) {
  console.error("dist-smoke/ is not there: run `npm run check:smoke` (it builds it) or leave PHONE_SKIP_BUILD unset.");
  process.exit(1);
}

// The same address rules as check-smoke.mjs: a recap's, a transcript's and an import's own address
// are served by the page that reads them.
function fileFor(url) {
  let path;
  try {
    path = decodeURIComponent(url.split(/[?#]/)[0]);
  } catch {
    return undefined;
  }
  if (/^\/account\/recaps\/[^/]+$/.test(path)) path = "/account/recaps";
  if (/^\/account\/house-rules\/[^/]+$/.test(path)) path = "/account/house-rules";
  if (/^\/account\/homebrew\/[^/]+$/.test(path)) path = "/account/homebrew";
  if (/^\/account\/import\/[^/]+$/.test(path)) path = "/account/import";
  if (/^\/account\/names\/[^/]+$/.test(path)) path = "/account/names";
  if (/^\/account\/transcripts\/[^/]+(\/\d+)?$/.test(path)) path = "/account/transcripts";
  const base = path === "/" ? "/index.html" : path;
  return [resolve(dist, "." + base), resolve(dist, "." + `${base}.html`)].find(
    (f) => f.startsWith(dist) && existsSync(f) && statSync(f).isFile(),
  );
}
const types = { ".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml", ".png": "image/png", ".webp": "image/webp", ".json": "application/json", ".woff2": "font/woff2", ".txt": "text/plain" };
const server = createServer((req, res) => {
  const file = fileFor(req.url ?? "/");
  if (!file) {
    res.writeHead(404).end("not found");
    return;
  }
  res.writeHead(200, { "content-type": types[file.slice(file.lastIndexOf("."))] ?? "application/octet-stream" }).end(readFileSync(file));
});
await new Promise((ok) => server.listen(0, "127.0.0.1", ok));
const origin = `http://127.0.0.1:${server.address().port}`;

let browser;
try {
  browser = await chromium.launch(process.env.A11Y_CHROME ? { executablePath: process.env.A11Y_CHROME } : { channel: "chrome" });
} catch (error) {
  console.error(`Could not start Chrome (${error.message.split("\n")[0]}). Install Google Chrome, or set A11Y_CHROME to a Chrome or Chromium binary.`);
  process.exit(1);
}

const REF = "200000000000000001.cmp1.1791000000";
const LONG_NAME = "session-with-a-really-long-file-name-that-goes-on-and-on-2026-09-14-final-v2.txt";

/** Runs in the page: what sticks out sideways, and which touch targets are too small or too close. */
function measure(vw) {
  // The width the phone has, passed in: a mobile browser widens its own layout window (innerWidth)
  // to fit whatever is too wide, so comparing with innerWidth could never fail (#2250 review).
  const out = { sw: document.documentElement.scrollWidth, vw, over: [], small: [], close: [] };
  const shown = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none";
  };
  const inOwnScrollBox = (el) => {
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      const o = getComputedStyle(p).overflowX;
      if ((o === "auto" || o === "scroll") && p.scrollWidth > p.clientWidth) return true;
    }
    return false;
  };
  const label = (el) => `${el.tagName.toLowerCase()} "${(el.getAttribute("aria-label") || el.textContent || el.getAttribute("type") || "").trim().slice(0, 28)}"`;
  for (const el of document.body.querySelectorAll("*")) {
    if (!shown(el)) continue;
    const r = el.getBoundingClientRect();
    if ((r.right > vw + 1 || r.left < -1) && !inOwnScrollBox(el)) out.over.push(`${label(el)} ends at ${Math.round(r.right)}`);
  }
  // A checkbox or radio is tapped through its label; the box the thumb has is that label's.
  const targets = [...document.body.querySelectorAll("button, a.button, input:not([type=hidden]), select, textarea, summary, [role=button]")]
    .filter(shown)
    .map((el) => ({ el, r: (el.matches("input[type=checkbox], input[type=radio]") ? el.closest("label") : null)?.getBoundingClientRect() ?? el.getBoundingClientRect() }));
  for (const { el, r } of targets) if (r.height < 43.5 || r.width < 43.5) out.small.push(`${label(el)} ${Math.round(r.width)}x${Math.round(r.height)}`);
  for (let i = 0; i < targets.length; i++) {
    for (let j = i + 1; j < targets.length; j++) {
      const a = targets[i].r, b = targets[j].r;
      if (targets[i].el.contains(targets[j].el) || targets[j].el.contains(targets[i].el)) continue;
      const gapX = Math.max(b.left - a.right, a.left - b.right);
      const gapY = Math.max(b.top - a.bottom, a.top - b.bottom);
      // Two targets closer than 8 px both ways, or overlapping. The one exemption: stacked,
      // full-width, thumb-tall disclosure rows (the questions on /pricing), which are not a mis-tap risk.
      const fullWidthRow = (r) => r.width >= vw - 48;
      const stackedRows =
        targets[i].el.tagName === "SUMMARY" && targets[j].el.tagName === "SUMMARY" && fullWidthRow(a) && fullWidthRow(b) && a.height >= 43.5 && b.height >= 43.5;
      if (gapX < 8 && gapY < 8 && !stackedRows) out.close.push(`${label(targets[i].el)} and ${label(targets[j].el)} (${Math.round(Math.max(gapX, gapY))} px)`);
    }
  }
  return out;
}

const adminIn = async (page) => {
  await page.getByLabel("Email").fill("owner@example.com");
  await page.getByLabel(/password/i).first().fill("anything at all here");
  await page.getByRole("button", { name: /sign in/i }).first().click();
  await page.getByText("Free access").first().waitFor();
};

/** Each page and the states to measure on it: [name, address, steps run before each measure]. */
const pages = [
  ["account, signed out", "/account?demo=signed-out"],
  ["account, no plan", "/account?demo=no-plan"],
  ["account, Table plan", "/account?demo=table"],
  ["account, grace", "/account?demo=grace"],
  ["account, offers", "/account?demo=offers"],
  ["account, free access", "/account?demo=free-paying"],
  ["recap list", "/account/recaps?demo=table"],
  ["a recap's page", `/account/recaps/${REF}?demo=table`],
  ["transcripts", "/account/transcripts?demo=table"],
  ["transcripts, signed out (the line at the top)", "/account/transcripts?demo=signed-out"],
  ["import: choose files", "/account/import/100.cmp1?demo=table"],
  ["import: the chosen files, each with Remove", "/account/import/100.cmp1?demo=table", async (page) => {
    await page.setInputFiles('input[type="file"]', [
      { name: LONG_NAME, mimeType: "text/plain", buffer: Buffer.from("Cerric: hello") },
      { name: "second-session.txt", mimeType: "text/plain", buffer: Buffer.from("Cerric: again") },
    ]);
    await page.getByRole("button", { name: "Remove all" }).waitFor();
    await page.getByRole("button", { name: "Remove second-session.txt" }).waitFor();
  }],
  ["import: with files, and ready", "/account/import/100.cmp1?demo=table", async (page) => {
    await page.setInputFiles('input[type="file"]', [
      { name: LONG_NAME, mimeType: "text/plain", buffer: Buffer.from("Cerric: hello\nAsh: hi\nMira: ok") },
      { name: "second-session.txt", mimeType: "text/plain", buffer: Buffer.from("Cerric: again") },
    ]);
    await page.getByRole("button", { name: "Preview" }).click();
    await page.getByRole("button", { name: /Check what will be kept/ }).click();
    await page.getByText(/^Checked:/).first().waitFor();
  }],
  ["names: the list", "/account/names/100.cmp1?demo=table"],
  ["names: a name open to change", "/account/names/100.cmp1?demo=table", async (page) => {
    // The longest name's row, so a long unbroken name is measured; open its editor, search for a
    // merge, and ask for the removal's confirmation, so every part of the editor is on the page.
    await page.getByLabel("Find a name").fill("Alaric");
    await page.getByRole("button", { name: /^Change Ser Alaric/ }).first().click();
    await page.getByLabel("Find the other name").fill("a");
    await page.getByRole("radio").first().waitFor();
    await page.getByRole("button", { name: "Remove this name" }).click();
    await page.getByRole("button", { name: "Yes, remove it" }).waitFor();
  }],
  ["names: not a DM", "/account/names/100.cmp1.names-manager?demo=table"],
  ["admin, signed in", "/admin", adminIn],
  ["admin, with an addition and the tickets shown", "/admin", async (page) => {
    await adminIn(page);
    // Fail loudly if a label changed: a state that silently measured nothing new would pass for nothing.
    await page.getByLabel(/Discord user ID/i).fill("123456789012345678");
    await page.getByRole("button", { name: /^(add|give|save)/i }).first().click();
    await page.getByText("123456789012345678").first().waitFor();
    // The tickets are listed once the filter is on All, each with its replies and its buttons: they
    // must be on the page.
    await page.getByRole("button", { name: /^All \(/ }).click();
    await page.getByText("My invoice shows the wrong plan").first().waitFor();
    await page.getByRole("button", { name: /mark answered/i }).first().waitFor();
  }],
  ["pricing", "/pricing"],
];

const failures = [];
for (const width of [360, 390]) {
  for (const [name, address, steps] of pages) {
    const context = await browser.newContext({ viewport: { width, height: 800 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
    const page = await context.newPage();
    page.setDefaultTimeout(10000);
    const problems = [];
    try {
      await page.goto(origin + address, { waitUntil: "load" });
      await page.locator("main").first().waitFor();
      if (steps) await steps(page);
      // PHONE_PROOF=<px> adds a block that wide to every page once its steps are done, to prove the check can fail.
      if (process.env.PHONE_PROOF) await page.evaluate((px) => { const d = document.createElement("div"); d.style.cssText = `width:${px}px;height:10px`; (document.querySelector("main") ?? document.body).append(d); }, Number(process.env.PHONE_PROOF));
      await page.waitForTimeout(500);
      const m = await page.evaluate(measure, width);
      if (m.sw > m.vw) problems.push(`the page scrolls sideways (${m.sw} px in a ${m.vw} px window)`);
      const list = (what, items, max) => {
        const all = [...new Set(items)];
        for (const item of all.slice(0, max)) problems.push(`${what}: ${item}`);
        if (all.length > max) problems.push(`${what}: and ${all.length - max} more`);
      };
      list("sticks out", m.over, 6);
      list("too small to tap", m.small, 8);
      list("too close", m.close, 6);
      if (shots) await page.screenshot({ path: resolve(shots, `${name.replace(/[^a-z0-9]+/gi, "-")}-${width}.png`), fullPage: true });
    } catch (error) {
      problems.push(error.message.split("\n")[0]);
    }
    for (const p of problems) failures.push(`${name} at ${width} px: ${p}`);
    console.log(`${problems.length === 0 ? "ok  " : "FAIL"} ${width} px  ${name}`);
    await context.close();
  }
}
await browser.close();
server.close();

if (failures.length) {
  console.error(`\n${failures.length} problem(s) at phone width:\n- ${failures.join("\n- ")}`);
  process.exit(1);
}
console.log(`\nPhone-width checks passed on ${pages.length} states at 360 and 390 px.`);
