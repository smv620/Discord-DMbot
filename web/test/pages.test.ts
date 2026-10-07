// One smoke test per page: it renders, has a title and a heading, and logs no errors.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { experimental_AstroContainer as AstroContainer } from "astro/container";
import type { AstroComponentFactory } from "astro/runtime/server/index.js";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import Home from "../src/pages/index.astro";
import Pricing from "../src/pages/pricing.astro";
import Install from "../src/pages/install.astro";
import Account from "../src/pages/account.astro";
import Terms from "../src/pages/legal/terms.astro";
import Privacy from "../src/pages/legal/privacy.astro";
import Refunds from "../src/pages/legal/refunds.astro";
import NotFound from "../src/pages/404.astro";

const pages: [path: string, page: AstroComponentFactory, title: string][] = [
  ["/", Home, "DMbot"],
  ["/pricing", Pricing, "Prices · DMbot"],
  ["/install", Install, "Add to Discord · DMbot"],
  ["/account", Account, "My account · DMbot"],
  ["/legal/terms", Terms, "Terms of use · DMbot"],
  ["/legal/privacy", Privacy, "Privacy · DMbot"],
  ["/legal/refunds", Refunds, "Refunds · DMbot"],
  ["/404", NotFound, "Page not found · DMbot"],
];

let container: AstroContainer;

beforeAll(async () => {
  container = await AstroContainer.create();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe.each(pages)("page %s", (path, page, title) => {
  it("renders with a title, one heading and no console errors", async () => {
    const errors = vi.spyOn(console, "error");
    const warnings = vi.spyOn(console, "warn");

    const html = await container.renderToString(page, {
      request: new Request(`https://dmbot.example${path}`),
    });

    expect(html).toContain(`<title>${title}</title>`);
    expect(html).toMatch(/<html lang="en"/);
    expect(html).toContain('name="viewport" content="width=device-width, initial-scale=1"');
    expect(html).toMatch(/<meta name="description" content="[^"]{20,}"/);
    expect(html.match(/<h1[\s>]/g)).toHaveLength(1);
    expect(errors).not.toHaveBeenCalled();
    expect(warnings).not.toHaveBeenCalled();
  });
});

describe("signed-in area", () => {
  it("keeps /account out of search results", async () => {
    const html = await container.renderToString(Account, {
      request: new Request("https://dmbot.example/account"),
    });
    expect(html).toContain('<meta name="robots" content="noindex"');
  });

  it("lets search engines index the public pages", async () => {
    const html = await container.renderToString(Pricing, {
      request: new Request("https://dmbot.example/pricing"),
    });
    expect(html).not.toContain('name="robots"');
  });
});

// The site must never use the game's trademarks or the publisher's name (README.md).
// Say "5e-compatible tabletop games" instead.
describe("no trademarks", () => {
  const banned = [
    /D\s*&(amp;)?\s*D/i,
    /Dungeons\s*(&|and)\s*Dragons/i,
    /Wizards of the Coast/i,
    /\bWotC\b/i,
    /D&D Beyond/i,
    /Forgotten Realms/i,
    /Player'?s Handbook/i,
    /Monster Manual/i,
  ];
  const root = fileURLToPath(new URL("..", import.meta.url));

  function files(dir: string): string[] {
    return readdirSync(dir).flatMap((name) => {
      const full = join(dir, name);
      return statSync(full).isDirectory() ? files(full) : [full];
    });
  }

  const sources = [...files(join(root, "src")), ...files(join(root, "public"))];

  it.each(sources.map((file) => [file.slice(root.length)]))("%s is clean", (relative) => {
    const text = readFileSync(join(root, relative), "utf8");
    for (const pattern of banned) {
      expect(text, `found ${pattern}`).not.toMatch(pattern);
    }
  });
});
