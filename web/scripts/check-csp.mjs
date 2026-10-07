// After `npm run build`: every inline script in dist/ must be allowed by its hash in
// dist/_headers (or the browser blocks it and /account stops working), and only /account
// may have scripts at all. Usage: node scripts/check-csp.mjs [dist-dir]
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join, relative } from "node:path";

import { htmlFiles, inlineScriptHashes } from "./csp.mjs";

const dist = process.argv[2] ?? new URL("../dist/", import.meta.url).pathname;
const policy = readFileSync(join(dist, "_headers"), "utf8").match(
  /^\s*Content-Security-Policy:[^\n]*?script-src ([^;\n]*);/m,
)?.[1];
const problems = [];
if (!policy) problems.push("dist/_headers has no script-src");

for (const page of htmlFiles(dist)) {
  const name = relative(dist, page);
  const html = readFileSync(page, "utf8");
  if (name !== "account.html" && /<script/i.test(html)) {
    problems.push(`${name} has a script; only account.html should`);
  }
  for (const hash of inlineScriptHashes(html)) {
    if (!policy?.split(" ").includes(hash)) problems.push(`${name}: inline script ${hash} not allowed`);
  }
}

// A real build must not contain the pretend API (src/account/mock.ts): neither its chunk
// nor its marker. This reads the shell's PUBLIC_API_BASE only; a mock build set through
// web/.env instead fails here, which is the safe way round.
if (process.env.PUBLIC_API_BASE !== "mock") {
  const assets = join(dist, "_astro");
  for (const name of existsSync(assets) ? readdirSync(assets) : []) {
    if (
      /^mock\./.test(name) ||
      readFileSync(join(assets, name), "utf8").includes("dmbot-pretend-api-7f3c")
    ) {
      problems.push(`_astro/${name} contains the pretend API; build without PUBLIC_API_BASE=mock`);
    }
  }
}

if (problems.length > 0) {
  console.error(problems.join("\n"));
  process.exit(1);
}
console.log("Every inline script is allowed by its hash; only /account has scripts.");
