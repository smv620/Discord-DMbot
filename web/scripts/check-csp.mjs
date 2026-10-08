// After `npm run build`: every inline script in dist/ must be allowed by its hash in
// dist/_headers (or the browser blocks it and the page stops working), and only /account
// /hello (its forms, #665) and /admin (#772) may have scripts at all. Usage: node scripts/check-csp.mjs [dist-dir]
import { readFileSync } from "node:fs";
import { join, relative } from "node:path";

import { htmlFiles, inlineScriptHashes, mockLeaks, TURNSTILE_ORIGIN } from "./csp.mjs";

const SCRIPTED = new Set(["account.html", "hello.html", "admin.html"]);
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
if (process.env.PUBLIC_API_BASE !== "mock") problems.push(...mockLeaks(dist));

if (problems.length > 0) {
  console.error(problems.join("\n"));
  process.exit(1);
}
console.log("Every inline script is allowed by its hash; only /account, /hello and /admin have scripts.");
