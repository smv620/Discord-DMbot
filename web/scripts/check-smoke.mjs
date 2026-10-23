// One browser smoke run over the signed-in pages (#1488): build the site with the pretend API and
// the recap pages switched on (astro.smoke.config.mjs), serve it from dist-smoke/, and walk the
// pages the unit tests only cover one island at a time: My Account's questions, the recap list
// and a recap's page (names, the three choices), and the admin Tickets page (the week line, Needs
// the team, a thread, the reply box). Each page must draw its heading, raise no console error or
// uncaught exception, and make no request that fails: a broken import or island boundary fails
// here before it is deployed. Nothing is sent anywhere: the pretend API lives in the page.
//
// Needs Chrome or Chromium like check-a11y.mjs: set A11Y_CHROME to its path, or it uses the
// installed Google Chrome (the CI runners have one). SMOKE_SKIP_BUILD=1 reuses dist-smoke/.
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright-core";

const root = fileURLToPath(new URL("../", import.meta.url));
const dist = resolve(root, "dist-smoke") + "/";

if (!process.env.SMOKE_SKIP_BUILD) {
  const built = spawnSync("npx", ["astro", "build", "--config", "astro.smoke.config.mjs"], {
    cwd: root,
    stdio: "inherit",
    // A mock build refuses the live branch (astro.config.mjs): make sure CI's branch name can't trip it.
    env: { ...process.env, PUBLIC_API_BASE: "mock", CF_PAGES_BRANCH: "" },
  });
  if (built.status !== 0) {
    console.error("The smoke build failed.");
    process.exit(1);
  }
}

// "/help" is dist-smoke/help.html (format "file"); a recap's own address is served by recaps.html,
// as the live site does with a redirect rule.
function fileFor(url) {
  let path;
  try {
    path = decodeURIComponent(url.split(/[?#]/)[0]);
  } catch {
    return undefined;
  }
  if (/^\/account\/recaps\/[^/]+$/.test(path)) path = "/account/recaps";
  if (/^\/account\/house-rules\/[^/]+$/.test(path)) path = "/account/house-rules";
  if (/^\/account\/timeline\/[^/]+$/.test(path)) path = "/account/timeline";
  if (/^\/account\/homebrew\/[^/]+$/.test(path)) path = "/account/homebrew";
  if (/^\/account\/names\/[^/]+$/.test(path)) path = "/account/names";
  if (/^\/account\/web\/[^/]+$/.test(path)) path = "/account/web";
  if (/^\/account\/npc\/[^/]+\/[^/]+$/.test(path)) path = "/account/npc";
  if (/^\/account\/campaigns\/[^/]+$/.test(path)) path = "/account/campaigns";
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
  browser = await chromium.launch(
    process.env.A11Y_CHROME ? { executablePath: process.env.A11Y_CHROME } : { channel: "chrome" },
  );
} catch (error) {
  console.error(`Could not start Chrome (${error.message.split("\n")[0]}). Install Google Chrome, or set A11Y_CHROME to a Chrome or Chromium binary.`);
  process.exit(1);
}

const failures = [];

/** One page: open it, run the steps, and report anything the browser itself complained about. */
async function visit(name, address, steps) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  const problems = [];
  page.on("console", (m) => m.type() === "error" && problems.push(`console error: ${m.text()}`));
  page.on("pageerror", (e) => problems.push(`uncaught: ${e.message}`));
  page.on("requestfailed", (r) => problems.push(`request failed: ${r.url()} (${r.failure()?.errorText})`));
  page.on("response", (r) => r.status() >= 400 && problems.push(`HTTP ${r.status()}: ${r.url()}`));
  try {
    await page.goto(origin + address, { waitUntil: "load" });
    await steps(page);
  } catch (error) {
    problems.push(`${error.message.split("\n")[0]}`);
  }
  for (const p of problems) failures.push(`${name} (${address}): ${p}`);
  console.log(`${problems.length === 0 ? "ok  " : "FAIL"} ${name}`);
  await context.close();
}

const seen = (page, text) => page.getByText(text, { exact: false }).first().waitFor();

/** One named step, so a failure says which one broke instead of "timeout". */
async function step(name, action) {
  try {
    return await action();
  } catch (error) {
    throw new Error(`${name}: ${error.message.split("\n")[0]}`);
  }
}

await visit("My Account: the person's questions", "/account", async (page) => {
  await page.getByRole("heading", { level: 1 }).first().waitFor();
  // Send a question (nothing leaves the page: the pretend API keeps it), and it is listed.
  await step("the question form", async () => {
    await page.getByLabel("Subject", { exact: true }).fill("Smoke test question");
    await page.getByLabel("What can we help with?", { exact: true }).fill("Nothing is wrong; this is the smoke run.");
    await page.getByRole("button", { name: "Send to the team", exact: true }).click();
  });
  await step("Your questions", () => page.getByRole("heading", { name: "Your questions" }).waitFor());
  await step("the new question is listed", () => seen(page, "Smoke test question"));
});

await visit("The recap list", "/account/recaps", async (page) => {
  await page.getByRole("heading", { level: 1, name: "Your recaps" }).waitFor();
  await seen(page, "The ferry at dusk");
});

await visit("A recap: read it, names, choices", "/account/recaps/200000000000000001.cmp1.1791000000", async (page) => {
  await page.getByRole("heading", { level: 1 }).first().waitFor();
  await page.getByRole("heading", { name: "The ferry at dusk" }).first().waitFor();
  await seen(page, "The ferry ferryman".split(" ")[0]); // the text of the recap
  // The names part opens and lists the campaign's names.
  await page.getByText("Names in this campaign").click();
  await seen(page, "Ka'zeth");
  // The three choices: all there, and one saves and says so.
  for (const label of ["Write a recap after each session", "Make the narration and music", "Email me when it is ready"]) {
    await page.getByLabel(label, { exact: false }).waitFor();
  }
  await page.getByLabel("Email me when it is ready", { exact: false }).uncheck();
  await seen(page, "Saved. No email.");
});

await visit("House rules: campaigns, a campaign's rules and its followed set", "/account/house-rules", async (page) => {
  await page.getByRole("heading", { level: 1, name: "House rules" }).waitFor();
  await step("the campaign list", () => seen(page, "The Brynwater Crossing"));
  await step("a campaign's rules", async () => {
    await page.getByRole("link", { name: "Read: The Brynwater Crossing" }).click();
    await seen(page, "2. Potions are a bonus action");
  });
  await step("the followed set", () => seen(page, "Red Table house rules, v4"));
  await step("the download button", () => page.getByRole("button", { name: "Save as a text file" }).waitFor());
});

await visit("Story timeline: sessions in order, with the recap and what came out", "/account/timeline/100.cmp1", async (page) => {
  await page.getByRole("heading", { level: 1, name: "Story timeline" }).waitFor();
  await step("session 1", () => seen(page, "Session 1"));
  await step("its recap", () => seen(page, "The river crossing"));
  await step("what came out at the table", () => seen(page, "Ferryman Odo, Hollow Mill"));
});

await visit("Shared rules: what a campaign shares and uses", "/account/sharing", async (page) => {
  await page.getByRole("heading", { level: 1, name: "Shared rules" }).waitFor();
  await step("the campaign", () => seen(page, "The Brynwater Crossing"));
  await step("what it shares", () => seen(page, "Brynwater table rules"));
  await step("what it uses", () => seen(page, "Frostmaiden pack"));
  await step("where to change it", () => seen(page, "Change it in Discord: ⚙️ Settings → 📚 Shared rules"));
});

await visit("Homebrew: campaigns, a campaign's shared entries by kind", "/account/homebrew", async (page) => {
  await page.getByRole("heading", { level: 1, name: "Homebrew" }).waitFor();
  await step("the campaign list", () => seen(page, "The Brynwater Crossing"));
  await step("a campaign's entries", async () => {
    await page.getByRole("link", { name: "Read: The Brynwater Crossing" }).click();
    await seen(page, "Frost Lance");
  });
  await step("grouped by kind", () => page.getByRole("heading", { name: "Spells" }).first().waitFor());
  await step("the download button", () => page.getByRole("button", { name: "Save as a text file" }).waitFor());
});

await visit("Transcripts: campaigns, sessions, a session, the two versions", "/account/transcripts", async (page) => {
  await page.getByRole("heading", { level: 1, name: "Session transcripts" }).waitFor();
  await step("the campaign list", () => seen(page, "The Brynwater Crossing"));
  await step("a campaign's sessions", async () => {
    await page.getByRole("link", { name: "Read: The Brynwater Crossing" }).click();
    await page.getByRole("heading", { name: "The Brynwater Crossing" }).waitFor();
  });
  await step("a session", async () => {
    await page.getByRole("link", { name: "Read: Session 2" }).click();
    await seen(page, "Ka'zeth draws his sword");
  });
  await step("as heard", async () => {
    await page.getByRole("button", { name: "As heard" }).click();
    await seen(page, "Kazeth draws his sword");
  });
  await step("the download button", () => page.getByRole("button", { name: "Save as a text file" }).waitFor());
});

await visit("The admin: tickets", "/admin", async (page) => {
  await page.getByRole("heading", { level: 1 }).first().waitFor();
  // The pretend API takes any email and password.
  await step("email box", () => page.getByLabel("Email", { exact: true }).fill("owner@example.com"));
  await step("password box", () => page.getByLabel("Password", { exact: true }).fill("not-a-real-password"));
  await step("sign in button", () => page.getByRole("button", { name: "Sign in", exact: true }).click());
  await step("signed in", () => seen(page, "Signed in as owner@example.com"));
  await step("tickets heading", () => page.getByRole("heading", { name: "Support tickets" }).waitFor());
  await step("the week line", () => seen(page, "This week:"));
  await step("Needs the team filter", () => page.getByRole("button", { name: /Needs the team/ }).click());
  await step("how long each has waited", () => seen(page, "waiting "));
  await step("a ticket thread", () => page.getByRole("heading", { name: "Messages after the first" }).first().waitFor());
  const reply = page.getByLabel(/^Your reply to/).first();
  await step("the reply box", () => reply.fill("A reply that is typed but never sent."));
  if ((await reply.inputValue()) !== "A reply that is typed but never sent.") throw new Error("the reply box did not keep what was typed");
});

await browser.close();
server.close();

if (failures.length > 0) {
  console.error(`\nSmoke run failed:\n${failures.map((f) => `  ${f}`).join("\n")}`);
  process.exit(1);
}
console.log("\nSmoke run passed on 7 pages.");
