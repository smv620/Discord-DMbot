// Shared by csp-hashes.mjs (writes dist/_headers) and check-csp.mjs (checks it).
import { createHash } from "node:crypto";
import { readdirSync, statSync } from "node:fs";
import { join } from "node:path";

export function htmlFiles(dir) {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) return htmlFiles(full);
    return full.endsWith(".html") ? [full] : [];
  });
}

/** The CSP hash of every inline script in a page (scripts with src, and JSON data, skipped). */
export function inlineScriptHashes(html) {
  const hashes = [];
  for (const [, attrs = "", body] of html.matchAll(/<script(\s[^>]*)?>([\s\S]*?)<\/script>/gi)) {
    if (/\ssrc\s*=/i.test(attrs)) continue;
    if (/\stype\s*=\s*["']?application\/(ld\+)?json/i.test(attrs)) continue;
    hashes.push(`'sha256-${createHash("sha256").update(body, "utf8").digest("base64")}'`);
  }
  return hashes;
}

/** The API's origin if PUBLIC_API_BASE is a full address, else null (same site). */
export function apiOrigin(base) {
  if (!base || !/^https?:\/\//i.test(base)) return null;
  return new URL(base).origin;
}
