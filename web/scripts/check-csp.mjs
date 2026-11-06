// After `npm run build`: every inline script in dist/ must be allowed by its hash in
// dist/_headers (or the browser blocks it and the page stops working), and only /account
// /hello (its forms, #665), /help (its search box, #1939), /admin (#772) and /pricing (a personal link's greeting, #1231) may have scripts at all. Usage: node scripts/check-csp.mjs [dist-dir]
import { readFileSync } from "node:fs";
import { join, relative } from "node:path";

import { effectiveApiBase, htmlFiles, inlineScriptHashes, mockLeaks, TURNSTILE_ORIGIN } from "./csp.mjs";

// pricing.html: the greeting for a personal link (#1231), the only script on that page.
// account/recaps.html: the DM's recap pages (#1386).
// account/house-rules.html: the house-rules pages (#1521).
// account/homebrew.html: the homebrew pages (#1525).
// account/transcripts.html: the session transcript pages (#1501).
// account/import.html: the Import sessions page; it always loads its island and decides in the page
//   whether it is open, for the site or for a test server's campaign (#2260).
// account/names.html: the Names page, every name in a campaign, searched and fixed by its DMs (#2295).
// unsubscribe.html: an offer email's unsubscribe link (#1229).
// help.html: the search box that filters the steps in the page (#1939); it sends nothing anywhere.
const SCRIPTED = new Set([
  "account.html",
  "account/recaps.html",
  "account/house-rules.html",
  "account/homebrew.html",
  "account/sharing.html", // the Shared rules page (#2460)
  "account/timeline.html", // the Story timeline (#2462)
  "account/transcripts.html",
  "account/import.html", // the importer (#2107); open for the site or a test server (#2260)
  "account/names.html", // the Names page (#2295)
  "account/web.html", // the relationship web (#2324)
  "account/campaigns.html", // a campaign's own page (#2416)
  "account/npc.html", // an NPC's page and my notes (#2266)
  "handover.html", // the hand-over accept page (#2534)
  "r.html", // a shared recap (#2119): the island that reads the recap by its link
  "help.html",
  "hello.html",
  "admin.html",
  "pricing.html",
  "unsubscribe.html",
]);
const dist = process.argv[2] ?? new URL("../dist/", import.meta.url).pathname;
const policy = readFileSync(join(dist, "_headers"), "utf8").match(
  /^\s*Content-Security-Policy:[^\n]*?script-src ([^;\n]*);/m,
)?.[1];
const problems = [];
if (!policy) problems.push("dist/_headers has no script-src");

for (const page of htmlFiles(dist)) {
  const name = relative(dist, page);
  const html = readFileSync(page, "utf8");
  if (!SCRIPTED.has(name) && /<script/i.test(html)) {
    problems.push(`${name} has a script; only ${[...SCRIPTED].join(" and ")} should`);
  }
  for (const hash of inlineScriptHashes(html)) {
    if (!policy?.split(" ").includes(hash)) problems.push(`${name}: inline script ${hash} not allowed`);
  }
}

// A build with a Turnstile key must let its script and frame load, or the forms on /hello
// can never pass the check.
if (process.env.PUBLIC_TURNSTILE_SITE_KEY?.trim()) {
  const csp =
    readFileSync(join(dist, "_headers"), "utf8").match(/^\s*Content-Security-Policy:.*$/m)?.[0] ?? "";
  for (const directive of ["script-src", "frame-src"]) {
    const sources = csp.match(new RegExp(`${directive} ([^;\\n]*)`))?.[1]?.split(" ") ?? [];
    if (!sources.includes(TURNSTILE_ORIGIN)) problems.push(`${directive} doesn't allow Turnstile`);
  }
}

// A real build must not contain the pretend API (src/account/mock.ts): neither its chunk
// nor its marker. This reads the shell's PUBLIC_API_BASE only; a mock build set through
// web/.env instead fails here, which is the safe way round.
if (effectiveApiBase(process.env) !== "mock") problems.push(...mockLeaks(dist));

if (problems.length > 0) {
  console.error(problems.join("\n"));
  process.exit(1);
}
console.log("Every inline script is allowed by its hash; only /account, /account/recaps, /account/house-rules, /account/homebrew, /account/sharing, /account/timeline, /account/transcripts, /account/import, /account/names, /account/web, /account/campaigns, /account/npc, /handover, /hello, /help, /admin, /pricing and /unsubscribe have scripts.");
