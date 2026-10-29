// Plain words on the website (#1854; CLAUDE.md, Hard rules, "Simple enough for a child").
//
// The same check as core/tests/test_plain_words.py, from the same word list
// (docs/plain-words.json), so the bot and the website can't drift apart. It reads the string
// values the content files export and the visible text of the .astro pages and components, and
// fails on a banned word with the file and the text.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const WEB = join(dirname(fileURLToPath(import.meta.url)), "..");
const BANNED: Record<string, string> = JSON.parse(
  readFileSync(join(WEB, "..", "docs", "plain-words.json"), "utf8"),
).banned;

const escapeRegex = (word: string) => word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const WORDS = new RegExp(
  `\\b(${Object.keys(BANNED)
    .sort((a, b) => b.length - a.length)
    .map(escapeRegex)
    .join("|")})\\b`,
  "gi",
);

// Real exceptions: [file (relative to web/), the word as it is printed, why]. Each must still be
// hit, so a reworded page leaves the list (the stale-entry check below).
export const ALLOWED: [string, string, string][] = [
  [
    "src/pages/legal/privacy.astro",
    "database",
    "the privacy page must say what is stored and where; the wording is the owner's",
  ],
  [
    "src/pages/legal/privacy.astro",
    "exception",
    "\"the one exception\" is the ordinary English word, about an adventure file",
  ],
];

export function findings(text: string): string[] {
  return [...text.matchAll(WORDS)].map((m) => m[0].toLowerCase());
}

/** Every string a content module exports, however deep, with links and ids left out. */
export function stringsIn(value: unknown, key = ""): string[] {
  if (typeof value === "string") {
    const looksLikeALink = /^(\/|https?:|mailto:|#)/.test(value);
    const isAnId = /^(href|id|url|slug|path|file|name|key|kind|icon|class|type|to)$/i.test(key);
    return looksLikeALink || isAnId ? [] : [value];
  }
  if (Array.isArray(value)) return value.flatMap((v) => stringsIn(v, key));
  if (value && typeof value === "object") {
    return Object.entries(value)
      .filter(([k]) => !k.startsWith("_")) // a note for the editors, like "_comment"
      .flatMap(([k, v]) => stringsIn(v, k));
  }
  return [];
}

/** Text with every {…} expression removed, however deeply the braces nest. */
function dropExpressions(text: string): string {
  let out = "";
  let depth = 0;
  for (const char of text) {
    if (char === "{") depth += 1;
    else if (char === "}" && depth > 0) depth -= 1;
    else if (depth === 0) out += char;
  }
  return out;
}

/** The text a visitor reads in an .astro file: no frontmatter, scripts, styles or tags; the
 *  text people hear (alt, title, aria-label, placeholder) is kept. */
export function visibleText(source: string): string[] {
  const body = source.replace(/^---[\s\S]*?\n---/, "");
  const withoutCode = body
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<!--[\s\S]*?-->/g, " ");
  const attributes = [
    ...withoutCode.matchAll(/\b(?:alt|title|aria-label|placeholder)="([^"{]*)"/gi),
  ].map((m) => m[1] ?? "");
  const text = dropExpressions(withoutCode)
    .replace(/<[^>]*>/g, "\n")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  return [...text, ...attributes];
}

function walk(dir: string, ext: RegExp): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path, ext) : ext.test(name) ? [path] : [];
  });
}

const rel = (path: string) => relative(WEB, path);
const allowed = (file: string, word: string) => ALLOWED.some(([f, w]) => f === file && w === word);

async function everything(): Promise<[string, string][]> {
  const found: [string, string][] = [];
  for (const path of walk(join(WEB, "src", "content"), /\.ts$/)) {
    const { held: _held, ...shown } = await import(/* @vite-ignore */ path); // "held" is a note for the editors
    // A What's new file keeps its held reason inside `entry` (#2468): the same note for the editors.
    if (shown.entry && typeof shown.entry === "object") {
      const { held: _reason, ...visible } = shown.entry as Record<string, unknown>;
      shown.entry = visible;
    }
    for (const text of stringsIn(shown)) found.push([rel(path), text]);
  }
  for (const path of walk(join(WEB, "src"), /\.astro$/)) {
    for (const text of visibleText(readFileSync(path, "utf8"))) found.push([rel(path), text]);
  }
  return found;
}

describe("plain words on the website", () => {
  it("has no technical word in what a visitor reads", async () => {
    const bad = (await everything())
      .flatMap(([file, text]) =>
        findings(text)
          .filter((word) => !allowed(file, word))
          .map((word) => `${file}: "${word}" in "${text.slice(0, 80)}": say it in plain words (CLAUDE.md, Hard rules). Why: ${BANNED[word]}`),
      );
    expect(bad).toEqual([]);
    // (it imports every content module; on a busy machine that can pass the default 5 seconds)
  }, 30_000);

  it("keeps no stale allow-list entry", async () => {
    const hits = new Set((await everything()).flatMap(([f, t]) => findings(t).map((w) => `${f}|${w}`)));
    for (const [file, word, why] of ALLOWED) {
      expect(hits.has(`${file}|${word}`), `${file} no longer has "${word}": remove the entry`).toBe(true);
      expect(why.length).toBeGreaterThan(0);
    }
  }, 30_000);

  it("reads a real amount of text", async () => {
    const texts = (await everything()).map(([, t]) => t);
    expect(texts.length).toBeGreaterThan(300);
    expect(texts.some((t) => /DMbot/.test(t))).toBe(true);
  }, 30_000);

  it("sees a banned word, whole words only, in any case", () => {
    expect(findings("Your API key")).toEqual(["api"]);
    expect(findings("A rapid start, a modelling class")).toEqual([]);
    expect(findings("the Database and a token")).toEqual(["database", "token"]);
  });

  it("reads the visible text of a page and leaves code and tags out", () => {
    const page = `---\nconst database = 1;\n---\n<style>.token{}</style><h1 title="An API">Hello</h1><script>const api=1</script><p>Save {x} to the cache</p>`;
    expect(visibleText(page).join("|")).toContain("Hello");
    expect(findings(visibleText(page).join(" ")).sort()).toEqual(["api", "cache"]);
  });

  it("walks content values and skips links and ids", () => {
    expect(stringsIn({ a: ["Say hi", { href: "/x", text: "Go to the database" }], id: "api" })).toEqual([
      "Say hi",
      "Go to the database",
    ]);
  });
});
