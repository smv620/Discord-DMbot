// The CSP build step (scripts/csp.mjs): hashes must match what the browser computes.
import { createHash } from "node:crypto";

import { describe, expect, it } from "vitest";

import { apiOrigin, inlineScriptHashes } from "../scripts/csp.mjs";

const hash = (body: string): string =>
  `'sha256-${createHash("sha256").update(body, "utf8").digest("base64")}'`;

describe("inlineScriptHashes", () => {
  it("hashes the exact text between the tags", () => {
    const body = "\n  customElements.define('x', class {});\n";
    expect(inlineScriptHashes(`<p>hi</p><script>${body}</script>`)).toEqual([hash(body)]);
  });

  it("hashes module scripts but skips scripts with src and JSON data", () => {
    const html = [
      '<script type="module">a()</script>',
      '<script src="/_astro/x.js"></script>',
      "<script type='application/json'>{}</script>",
      '<script type="application/ld+json">{}</script>',
      "<SCRIPT>b()</SCRIPT>",
    ].join("");
    expect(inlineScriptHashes(html)).toEqual([hash("a()"), hash("b()")]);
  });
});

describe("apiOrigin", () => {
  it.each([
    [undefined, null],
    ["/api", null],
    ["mock", null],
    ["https://api.dmbot.example/v1/", "https://api.dmbot.example"],
  ])("%s → %s", (base, origin) => {
    expect(apiOrigin(base)).toBe(origin);
  });
});
