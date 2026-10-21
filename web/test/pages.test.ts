// One smoke test per page: it renders, has a title and a heading, and logs no errors.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { getContainerRenderer } from "@astrojs/preact/container-renderer";
import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { loadRenderers } from "astro:container";
import type { AstroComponentFactory } from "astro/runtime/server/index.js";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { parsePage } from "./dom";
import { notAffiliated } from "../src/content/legal";
import { publicPaths } from "../src/content/site";

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
  ["/account/recaps", "account/recaps.astro", "Your recaps · DMbot", true],
  ["/account/homebrew", "account/homebrew.astro", "Homebrew · DMbot", true],
  ["/account/house-rules", "account/house-rules.astro", "House rules · DMbot", true],
  ["/account/transcripts", "account/transcripts.astro", "Session transcripts · DMbot", true],
  ["/account/import", "account/import.astro", "Import sessions · DMbot", true],
  ["/account/names", "account/names.astro", "Names · DMbot", true],
  ["/r", "r.astro", "A shared recap · DMbot", true],
  ["/legal/terms", "legal/terms.astro", "Terms of use · DMbot", false],
  ["/legal/privacy", "legal/privacy.astro", "Privacy · DMbot", false],
  ["/legal/refunds", "legal/refunds.astro", "Refunds · DMbot", false],
  ["/credits", "credits.astro", "Credits · DMbot", false],
  ["/help", "help.astro", "Help · DMbot", false],
  ["/players", "players.astro", "Playing at a table · DMbot", false],
  ["/whats-new", "whats-new.astro", "What&#39;s new · DMbot", false],
  ["/hello", "hello.astro", "Say hello · DMbot", false],
  ["/admin", "admin.astro", "Admin · DMbot", true],
  ["/unsubscribe", "unsubscribe.astro", "Stop these emails · DMbot", true],
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
    expect(html.includes('<meta name="robots" content="noindex')).toBe(noindex);
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
    // The SRD licence asks for the publisher's name in its attribution statements (#1923), so the
    // Credits page may say "by Wizards of the Coast LLC" there, and the not-affiliated sentence.
    if (path === "/credits") html = html.replaceAll(licenceName, "").replace(notAffiliated, "");
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
    expect(nav.match(/<a /g)).toHaveLength(5);
    expect(nav.match(/<svg[^>]*aria-hidden="true"/g)).toHaveLength(5);
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

const licenceName = "Wizards of the Coast LLC";

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
    // Pictures are bytes, not words: random bytes can match a pattern. The share image's words
    // are the ones in this repo's own script of it, checked by eye and in the page text.
    (file) => file !== disclaimerFile && !/\.(png|webp)$/.test(file),
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
    let text = readFileSync(join(root, relative), "utf8");
    // The same one allowance as the rendered page: the name inside the licence's attribution statements.
    if (relative === join("src", "content", "credits.ts")) text = text.replaceAll(licenceName, "");
    for (const pattern of banned) {
      expect(text, `found ${pattern}`).not.toMatch(pattern);
    }
  });
});

it("keeps the admin page out of search engines and their link-following", async () => {
  const html = await container.renderToString(page("admin.astro"), {
    request: new Request("https://dmbot.example/admin"),
  });
  expect(html).toContain('<meta name="robots" content="noindex, nofollow">');
});

// Link previews and search files (#1145).
const webRoot = fileURLToPath(new URL("..", import.meta.url));

describe("link previews", () => {
  const rendered = new Map<string, Document>();
  const meta = (doc: Document, selector: string): string =>
    doc.querySelector(selector)?.getAttribute("content") ??
    doc.querySelector(selector)?.getAttribute("href") ??
    "";

  beforeAll(async () => {
    for (const [path, file] of pages) {
      const html = await container.renderToString(page(file), {
        request: new Request(`https://dmbot.example${path}`),
      });
      rendered.set(path, parsePage(html));
    }
  });

  const doc = (path: string): Document => {
    const found = rendered.get(path);
    if (!found) throw new Error(`not rendered: ${path}`);
    return found;
  };

  it("gives every page its own title and description, under 160 characters", () => {
    const titles = pages.map(([path]) => doc(path).title);
    const descriptions = pages.map(([path]) =>
      meta(doc(path), 'meta[name="description"]'),
    );
    expect(new Set(titles).size).toBe(pages.length);
    expect(new Set(descriptions).size).toBe(pages.length);
    for (const d of descriptions) expect(d.length).toBeLessThanOrEqual(160);
  });

  it("gives every page the Open Graph tags, a share image and a canonical link", () => {
    for (const [path] of pages) {
      const d = doc(path);
      expect(meta(d, 'meta[property="og:title"]'), path).toBe(d.title);
      expect(meta(d, 'meta[property="og:description"]'), path).toBe(
        meta(d, 'meta[name="description"]'),
      );
      expect(meta(d, 'meta[property="og:type"]'), path).toBe("website");
      expect(meta(d, 'meta[property="og:image"]'), path).toBe(
        "https://getdmbot.com/share.png",
      );
      expect(meta(d, 'meta[name="twitter:card"]'), path).toBe(
        "summary_large_image",
      );
      const url = new URL(path, "https://getdmbot.com").href;
      expect(meta(d, 'meta[property="og:url"]'), path).toBe(url);
      expect(meta(d, 'link[rel="canonical"]'), path).toBe(url);
    }
  });

  it("has a 1200 x 630 share image", () => {
    const png = readFileSync(join(webRoot, "public/share.png"));
    expect(png.subarray(1, 4).toString()).toBe("PNG");
    expect([png.readUInt32BE(16), png.readUInt32BE(20)]).toEqual([1200, 630]);
  });
});

describe("search files", () => {
  const publicPages = pages
    .filter(([, , , noindex]) => !noindex)
    .map(([path]) => path);

  it("lists exactly the public pages in the sitemap", () => {
    expect([...publicPaths].sort()).toEqual([...publicPages].sort());
  });

  it("builds the sitemap from that list, with no private page", async () => {
    const { GET } = await import("../src/pages/sitemap.xml");
    const res = await (
      GET as (c: { site: URL }) => Response | Promise<Response>
    )({
      site: new URL("https://getdmbot.com"),
    });
    const xml = await res.text();
    const locs = [...xml.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1]);
    expect(locs).toEqual(
      publicPaths.map((p) => new URL(p, "https://getdmbot.com").href),
    );
    expect(xml).not.toMatch(/\/(account|admin|404)/);
  });

  it("keeps robots.txt away from the signed-in and admin pages and points at the sitemap", () => {
    const robots = readFileSync(join(webRoot, "public/robots.txt"), "utf8");
    expect(robots).toMatch(/^Disallow: \/account$/m);
    expect(robots).toMatch(/^Disallow: \/admin$/m);
    expect(robots).toMatch(/^Disallow: \/unsubscribe$/m);
    expect(robots).toMatch(/^Sitemap: https:\/\/getdmbot\.com\/sitemap\.xml$/m);
  });
});
