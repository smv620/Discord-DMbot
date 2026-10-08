// After `npm run build`: every inline script in dist/ must be allowed by its hash in
// dist/_headers (or the browser blocks it and /account stops working), and only /account
// may have scripts at all. Usage: node scripts/check-csp.mjs [dist-dir]
import { readFileSync } from "node:fs";
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

if (problems.length > 0) {
  console.error(problems.join("\n"));
  process.exit(1);
}
console.log("Every inline script is allowed by its hash; only /account has scripts.");
