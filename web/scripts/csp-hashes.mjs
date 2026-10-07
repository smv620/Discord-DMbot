// After `astro build`: allow exactly the inline scripts Astro wrote (the island loader on
// /account) by adding their sha256 hashes to script-src in dist/_headers. Keeps the
// Content-Security-Policy strict (no 'unsafe-inline' for scripts) without hand-copied
// hashes that break when Astro changes its loader.
import { createHash } from "node:crypto";
import { readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const dist = new URL("../dist/", import.meta.url).pathname;
const headersFile = join(dist, "_headers");

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    return statSync(full).isDirectory() ? files(full) : [full];
  });
}

const hashes = new Set();
for (const page of files(dist).filter((f) => f.endsWith(".html"))) {
  const html = readFileSync(page, "utf8");
  // Inline scripts only: <script> or <script type="module"> without src.
  for (const [, attrs, body] of html.matchAll(/<script(\s[^>]*)?>([\s\S]*?)<\/script>/g)) {
    if (attrs && /\ssrc=/.test(attrs)) continue;
    if (attrs && /type="application\/(ld\+)?json"/.test(attrs)) continue;
    hashes.add(`'sha256-${createHash("sha256").update(body, "utf8").digest("base64")}'`);
  }
}

const headers = readFileSync(headersFile, "utf8");
if (!/script-src 'self';/.test(headers)) {
  console.error("dist/_headers has no \"script-src 'self';\" to extend");
  process.exit(1);
}
const sources = ["'self'", ...[...hashes].sort()].join(" ");
writeFileSync(headersFile, headers.replace("script-src 'self';", `script-src ${sources};`));
console.log(`script-src allows ${hashes.size} inline script(s) by hash.`);
