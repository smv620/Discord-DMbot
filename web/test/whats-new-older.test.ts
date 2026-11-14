// The What's new page shows the newest entries only, and "Older changes" holds the rest (#4200): the page listed every entry ever
// made, so it grew past its 52 KB HTML budget and blocked every PR that added one. No entry is edited or dropped, and held entries
// are on neither page.
import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { beforeAll, describe, expect, it } from "vitest";

import { parsePage } from "./dom";

import { RECENT_DAYS, RECENT_MIN, entries, olderHeading, splitEntries, type Entry } from "../src/content/whats-new";
import Older from "../src/pages/whats-new/older.astro";
import WhatsNew from "../src/pages/whats-new.astro";

const day = (n: number, from = "2026-11-12"): string => new Date(Date.parse(`${from}T00:00:00Z`) - n * 86_400_000).toISOString().slice(0, 10);
const made = (count: number, spacing: number): Entry[] =>
  Array.from({ length: count }, (_, i) => ({ date: day(i * spacing), title: `Entry ${i}`, what: "Something." }));
const NOW = new Date("2026-11-12T10:00:00Z");

describe("splitEntries", () => {
  it("keeps the recent days when that is more than 30 entries", () => {
    // three a day for 40 days
    const all = Array.from({ length: 120 }, (_, i) => ({ date: day(Math.floor(i / 3)), title: `Entry ${i}`, what: "Something." }));
    const { recent, older } = splitEntries(all, NOW);
    expect(recent.length).toBe((RECENT_DAYS + 1) * 3); // (today back to RECENT_DAYS days ago, both days)
    expect(recent.every((e) => e.date >= day(RECENT_DAYS))).toBe(true);
    expect(older.every((e) => e.date < day(RECENT_DAYS))).toBe(true);
  });

  it("keeps at least the newest 30 when fewer are recent", () => {
    const all = made(50, 10); // one every ten days: only 2 inside the recent days
    const { recent, older } = splitEntries(all, NOW);
    expect(recent.length).toBe(RECENT_MIN);
    expect(older.length).toBe(20);
    expect(recent[0]).toEqual(all[0]);
  });

  it("puts every entry on exactly one side, in order", () => {
    const all = made(120, 2);
    const { recent, older } = splitEntries(all, NOW);
    expect([...recent, ...older]).toEqual(all);
  });

  it("has no older page entries when there are few entries", () => {
    expect(splitEntries(made(10, 5), NOW).older).toEqual([]);
  });
});

describe("the two pages", () => {
  let main: Document;
  let older: Document;
  let mainHtml = "";
  let olderHtml = "";
  beforeAll(async () => {
    const container = await AstroContainer.create();
    mainHtml = await container.renderToString(WhatsNew, { request: new Request("https://dmbot.example/whats-new") });
    olderHtml = await container.renderToString(Older, { request: new Request("https://dmbot.example/whats-new/older") });
    main = parsePage(mainHtml);
    older = parsePage(olderHtml);
  });
  const titles = (doc: Document): string[] => [...doc.querySelectorAll("main .entry h2")].map((h) => h.textContent ?? "");

  it("the main page holds exactly the entries the rule picks", () => {
    expect(titles(main)).toEqual(splitEntries(entries, new Date()).recent.map((e) => e.title));
  });

  it("every visible entry is on exactly one of the two pages", () => {
    const both = [...titles(main), ...titles(older)];
    expect(both).toEqual(entries.map((e) => e.title));
  });

  it("the main page links to the older one, and the older one links back", () => {
    if (splitEntries(entries, new Date()).older.length > 0) {
      expect(main.querySelector('main a[href="/whats-new/older"]')?.textContent).toBe(olderHeading);
    }
    expect(older.querySelector('main a[href="/whats-new"]')).toBeTruthy();
  });

  it("both pages leave room for another entry under the 52 KB budget", () => {
    // (the build measures the real files: this keeps the rule honest; a new entry is about 0.5 KB)
    const kb = (html: string): number => Buffer.byteLength(html) / 1024;
    expect(kb(mainHtml) + 0.6).toBeLessThan(52);
    expect(kb(olderHtml) + 0.6).toBeLessThan(52);
  });
});
