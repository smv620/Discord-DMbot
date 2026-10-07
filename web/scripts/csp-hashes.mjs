// After `astro build`: finish the Content-Security-Policy in dist/_headers.
// - script-src: allow exactly the inline scripts Astro wrote (the island loader on
//   /account) by their sha256 hashes, so scripts never need 'unsafe-inline'.
// - connect-src: add the web API's origin when PUBLIC_API_BASE is a full address.
// Usage: node scripts/csp-hashes.mjs [dist-dir]
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { apiOrigin, htmlFiles, inlineScriptHashes } from "./csp.mjs";

const dist = process.argv[2] ?? new URL("../dist/", import.meta.url).pathname;
const headersFile = join(dist, "_headers");

const hashes = new Set();
for (const page of htmlFiles(dist)) {
  for (const hash of inlineScriptHashes(readFileSync(page, "utf8"))) hashes.add(hash);
}

let headers = readFileSync(headersFile, "utf8");
if (!headers.includes("script-src 'self';") || !headers.includes("connect-src 'self';")) {
  console.error("dist/_headers must contain \"script-src 'self';\" and \"connect-src 'self';\"");
  process.exit(1);
}
headers = headers.replace(
  "script-src 'self';",
  `script-src ${["'self'", ...[...hashes].sort()].join(" ")};`,
);
const origin = apiOrigin(process.env.PUBLIC_API_BASE);
if (origin) headers = headers.replace("connect-src 'self';", `connect-src 'self' ${origin};`);
writeFileSync(headersFile, headers);
console.log(
  `script-src allows ${hashes.size} inline script(s) by hash` +
    (origin ? `; connect-src allows ${origin}.` : "."),
);
