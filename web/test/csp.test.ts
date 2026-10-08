// The CSP build step (scripts/csp.mjs): hashes must match what the browser computes.
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { apiOrigin, inlineScriptHashes, MOCK_MARKER, mockLeaks } from "../scripts/csp.mjs";
import { MOCK_MARKER as APP_MARKER } from "../src/account/mock";

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

describe("mockLeaks (a real build must not ship the pretend API)", () => {
  function build(files: Record<string, string>): string {
    const dist = mkdtempSync(join(tmpdir(), "dmbot-dist-"));
    mkdirSync(join(dist, "_astro"));
    for (const [name, body] of Object.entries(files)) writeFileSync(join(dist, "_astro", name), body);
    return dist;
  }

  it("uses the same marker as the pretend API", () => {
    expect(MOCK_MARKER).toBe(APP_MARKER);
  });

  it("finds the mock chunk by name or by its marker, and passes a clean build", () => {
    const dist = build({
      "mock.Ab12.js": "export {}",
      "Account.Cd34.js": `const x = "${MOCK_MARKER}";`,
      "client.Ef56.js": "export const ok = 1;",
    });
    try {
      expect(mockLeaks(dist)).toEqual([
        "_astro/Account.Cd34.js contains the pretend API; build without PUBLIC_API_BASE=mock",
        "_astro/mock.Ab12.js contains the pretend API; build without PUBLIC_API_BASE=mock",
      ]);
    } finally {
      rmSync(dist, { recursive: true });
    }
    const clean = build({ "client.Ef56.js": "export const ok = 1;" });
    try {
      expect(mockLeaks(clean)).toEqual([]);
    } finally {
      rmSync(clean, { recursive: true });
    }
  });
});
