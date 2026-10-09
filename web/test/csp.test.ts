// The CSP build step (scripts/csp.mjs): hashes must match what the browser computes.
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { addDevHeaders, apiOrigin, effectiveApiBase, inlineScriptHashes, isDevSite, MOCK_MARKER, mockLeaks } from "../scripts/csp.mjs";
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

describe("effectiveApiBase (the development site, #837)", () => {
  const real = "https://api.dmbot.example";
  it("gives the development branch the real API, even when previews are set to the pretend one", () => {
    const env = { CF_PAGES_BRANCH: "development", PUBLIC_API_BASE: "mock", PUBLIC_DEV_API_BASE: real };
    expect(effectiveApiBase(env)).toBe(real);
    expect(apiOrigin(effectiveApiBase(env))).toBe(real);
    expect(isDevSite(env)).toBe(true);
  });

  it("keeps the pretend API for every other preview", () => {
    const env = { CF_PAGES_BRANCH: "webdev/837", PUBLIC_API_BASE: "mock", PUBLIC_DEV_API_BASE: real };
    expect(effectiveApiBase(env)).toBe("mock");
    expect(isDevSite(env)).toBe(false);
  });

  it("never switches production, and falls back when the variable is empty", () => {
    expect(effectiveApiBase({ CF_PAGES_BRANCH: "main", PUBLIC_API_BASE: "/api", PUBLIC_DEV_API_BASE: real })).toBe("/api");
    expect(effectiveApiBase({ CF_PAGES_BRANCH: "development", PUBLIC_API_BASE: "mock", PUBLIC_DEV_API_BASE: " " })).toBe("mock");
  });
});

describe("addDevHeaders (noindex for the development site only)", () => {
  const headers = "# note\n/*\n  X-Frame-Options: DENY\n\n/admin\n  X-Robots-Tag: noindex, nofollow\n";

  it("never marks the live site (main) or a preview as noindex", () => {
    expect(isDevSite({ CF_PAGES_BRANCH: "main" })).toBe(false);
    expect(addDevHeaders(headers, { CF_PAGES_BRANCH: "main" })).toBe(headers);
    expect(addDevHeaders(headers, { CF_PAGES_BRANCH: "webdev/837" })).toBe(headers);
    expect(addDevHeaders(headers, {})).toBe(headers);
  });

  it("adds noindex once, inside the /* block, for the development branch", () => {
    const out = addDevHeaders(headers, { CF_PAGES_BRANCH: "development" });
    expect(out).toContain("/*\n  X-Robots-Tag: noindex, nofollow\n  X-Frame-Options: DENY");
    expect(out.match(/\/\*\n {2}X-Robots-Tag/g)).toHaveLength(1);
  });

  it("refuses a headers file with no /* block", () => {
    expect(() => addDevHeaders("/admin\n  X: y\n", { CF_PAGES_BRANCH: "development" })).toThrow(/"\/\*" block/);
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
