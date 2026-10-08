// One smoke test per page: it renders, has a title and a heading, and logs no errors.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { getContainerRenderer } from "@astrojs/preact/container-renderer";
import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { loadRenderers } from "astro:container";
import type { AstroComponentFactory } from "astro/runtime/server/index.js";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { notAffiliated } from "../src/content/legal";

// Every file in src/pages gets a smoke test; a new page without an entry here fails below.
const found = import.meta.glob<{ default: AstroComponentFactory }>("../src/pages/**/*.astro", {
  eager: true,
});

function page(file: string): AstroComponentFactory {
  const module = found[`../src/pages/${file}`];
  if (!module) throw new Error(`no page ${file}`);
  return module.default;
}

// [path, file, tab title, kept out of search results]
const pages: [path: string, file: string, title: string, noindex: boolean][] = [
  ["/", "index.astro", "DMbot", false],
  ["/pricing", "pricing.astro", "Prices · DMbot", false],
  ["/install", "install.astro", "Add to Discord · DMbot", false],
  ["/account", "account.astro", "My Account · DMbot", true],
  ["/legal/terms", "legal/terms.astro", "Terms of use · DMbot", false],
  ["/legal/privacy", "legal/privacy.astro", "Privacy · DMbot", false],
  ["/legal/refunds", "legal/refunds.astro", "Refunds · DMbot", false],
  ["/hello", "hello.astro", "Say hello · DMbot", false],
  ["/404", "404.astro", "Page not found · DMbot", true],
];

it("has a smoke test for every page", () => {
  const listed = pages.map(([, file]) => `../src/pages/${file}`).sort();
  expect(Object.keys(found).sort()).toEqual(listed);
});

let container: AstroContainer;

beforeAll(async () => {
  // /account has a Preact island, so the container needs Preact's renderer.
  container = await AstroContainer.create({
    renderers: await loadRenderers([getContainerRenderer()]),
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe.each(pages)("page %s", (path, file, title, noindex) => {
  it("renders with a title and one heading, without logging to the console", async () => {
    const errors = vi.spyOn(console, "error");
    const warnings = vi.spyOn(console, "warn");

    const html = await container.renderToString(page(file), {
      request: new Request(`https://dmbot.example${path}`),
    });

    expect(html).toContain(`<title>${title}</title>`);
    expect(html).toMatch(/<html lang="en"/);
    expect(html).toContain('name="viewport" content="width=device-width, initial-scale=1"');
    expect(html).toMatch(/<meta name="description" content="[^"]{20,}"/);
    expect(html.match(/<h1[\s>]/g)).toHaveLength(1);
    expect(errors).not.toHaveBeenCalled();
    expect(warnings).not.toHaveBeenCalled();
    // Astro's own logger may bypass console; these spies catch what pages and components log.
  });

  it(noindex ? "is kept out of search results" : "may be listed by search engines", async () => {
    const html = await container.renderToString(page(file), {
      request: new Request(`https://dmbot.example${path}`),
    });
    expect(html.includes('<meta name="robots" content="noindex"')).toBe(noindex);
  });
});

describe("rendered pages", () => {
  // The source scan below skips content/legal.ts; this catches the not-affiliated sentence
  // (or anything else) being rendered where it shouldn't be. Only /legal/terms may name
  // the publisher, and only in that sentence.
  it.each(pages)("%s names no trademark", async (path, file) => {
    let html = await container.renderToString(page(file), {
      request: new Request(`https://dmbot.example${path}`),
    });
    if (path === "/legal/terms") html = html.replace(notAffiliated, "");
    for (const pattern of trademarks) {
      expect(html, `found ${pattern}`).not.toMatch(pattern);
    }
  });
});

describe("menu", () => {
  it("marks only the current page", async () => {
    const html = await container.renderToString(page("pricing.astro"), {
      request: new Request("https://dmbot.example/pricing"),
    });
    expect(html.match(/aria-current="page"/g)).toHaveLength(1);
    expect(html).toMatch(/<a href="\/pricing" aria-current="page"/);
  });

  it("links to the questions from the menu and the front page", async () => {
    const home = await container.renderToString(page("index.astro"), {
      request: new Request("https://dmbot.example/"),
    });
    expect(home).toMatch(/<a href="\/pricing#questions"[^>]*>(?:(?!<\/a>)[\s\S])*Q&amp;A\s*<\/a>/);
    expect(home).toMatch(/<a href="\/pricing#questions"[^>]*>Questions about plans and hours\? Read/);
    const pricing = await container.renderToString(page("pricing.astro"), {
      request: new Request("https://dmbot.example/pricing"),
    });
    expect(pricing).toMatch(/<section id="questions"/);
  });

  it("gives every menu item an icon hidden from screen readers", async () => {
    const html = await container.renderToString(page("index.astro"), {
      request: new Request("https://dmbot.example/"),
    });
    const nav = html.slice(html.indexOf('<nav aria-label="Main"'), html.indexOf("</nav>"));
    expect(nav.match(/<a /g)).toHaveLength(4);
    expect(nav.match(/<svg[^>]*aria-hidden="true"/g)).toHaveLength(4);
  });

  // A static build renders /pricing as pricing.html, and the page sees that path.
  it.each([
    ["/pricing.html", "/pricing"],
    ["/account.html", "/account"],
  ])("marks the current page when built as %s", async (built, link) => {
    const file = `${link.slice(1)}.astro`;
    const html = await container.renderToString(page(file), {
      request: new Request(`https://dmbot.example${built}`),
    });
    expect(html).toMatch(new RegExp(`<a href="${link}" aria-current="page"`));
  });
});

// The site must never use the game's trademarks or the publisher's name (README.md).
// Say "5e-compatible tabletop games" instead. README.md names them on purpose, so it isn't scanned.
const trademarks = [
  /D\s*&(amp;)?\s*D/i,
  /Dungeons\s*(&|and)\s*Dragons/i,
  /Wizards of the Coast/i,
  /\bWotC\b/i,
  /D&D Beyond/i,
  /Forgotten Realms/i,
  /Player['’]?s Handbook/i,
  /Monster Manual/i,
];

describe("no trademarks", () => {
  const banned = trademarks;
  const root = fileURLToPath(new URL("..", import.meta.url));

  function files(dir: string): string[] {
    return readdirSync(dir).flatMap((name) => {
      const full = join(dir, name);
      return statSync(full).isDirectory() ? files(full) : [full];
    });
  }

  // The one allowed mention: src/content/legal.ts says we are *not* connected to the
  // publisher. That file may hold that sentence and nothing else that matches.
  const disclaimerFile = join(root, "src", "content", "legal.ts");
  const sources = [...files(join(root, "src")), ...files(join(root, "public"))].filter(
    (file) => file !== disclaimerFile,
  );

  it("allows the publisher's name only in the not-affiliated sentence", () => {
    const text = readFileSync(disclaimerFile, "utf8").replace(
      "DMbot is not affiliated with or endorsed by Wizards of the Coast.",
      "",
    );
    for (const pattern of banned) {
      expect(text, `found ${pattern}`).not.toMatch(pattern);
    }
  });

  it.each(sources.map((file) => [file.slice(root.length)]))("%s is clean", (relative) => {
    const text = readFileSync(join(root, relative), "utf8");
    for (const pattern of banned) {
      expect(text, `found ${pattern}`).not.toMatch(pattern);
    }
  });
});
