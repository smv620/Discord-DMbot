// The What's new page shows the newest entries only, and "Older changes" holds the rest (#4200): the page listed every entry ever
// made, so it grew past its 52 KB HTML budget and blocked every PR that added one. No entry is edited or dropped, and held entries
// are on neither page.
import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { beforeAll, describe, expect, it } from "vitest";

import { parsePage } from "./dom";

import { MONTH_PAGE_BYTES, RECENT_DAYS, RECENT_MIN, entries, monthName, monthPages, olderHeading, splitEntries, type Entry } from "../src/content/whats-new";
import Month from "../src/pages/whats-new/[month].astro";
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

describe("older changes by month (#4203)", () => {
  const in_month = (month: string, count: number): Entry[] =>
    Array.from({ length: count }, (_, i) => ({ date: `${month}-${String(28 - (i % 28)).padStart(2, "0")}`, title: `${month} entry ${i}`, what: "Something." }));

  it("puts every entry on exactly one page, newest month first, and a page holds only its own month", () => {
    const all = [...in_month("2026-11", 10), ...in_month("2026-10", 5), ...in_month("2026-09", 1)];
    const pages = monthPages(all);
    expect(pages.map((p) => p.slug)).toEqual(["2026-11", "2026-10", "2026-09"]);
    expect(pages.flatMap((p) => p.entries).map((e) => e.title).sort()).toEqual(all.map((e) => e.title).sort());
    for (const p of pages) expect(p.entries.every((e) => e.date.startsWith(p.month))).toBe(true);
  });

  it("splits a month that is too long into parts of about equal size, in order, and keeps every entry once", () => {
    const month = in_month("2026-11", 450); // about 130 KB of entries: several parts
    const pages = monthPages(month);
    expect(pages.length).toBeGreaterThanOrEqual(3);
    expect(pages.map((p) => p.slug)).toEqual(pages.map((_, i) => `2026-11-${String.fromCharCode(97 + i)}`));
    expect(pages.every((p) => p.parts === pages.length && p.part === String.fromCharCode(65 + pages.indexOf(p)))).toBe(true);
    const counts = pages.map((p) => p.entries.length);
    expect(Math.max(...counts) - Math.min(...counts)).toBeLessThanOrEqual(2);
    expect(pages.flatMap((p) => p.entries)).toEqual(month);
  });

  it("does not split a month that fits, and has no pages with nothing older", () => {
    expect(monthPages(in_month("2026-11", 100)).map((p) => p.slug)).toEqual(["2026-11"]); // (about 30 KB)
    expect(monthPages([])).toEqual([]);
    expect(MONTH_PAGE_BYTES).toBeLessThan(52 * 1024);
  });

  it("names a month in words", () => {
    expect(monthName("2026-11")).toBe("November 2026");
    expect(monthName("2026-01")).toBe("January 2026");
  });

  it("a month's worth of new entries still leaves every page under the 52 KB budget", async () => {
    const container = await AstroContainer.create();
    const longWords = "A longer line of plain words, about what a real entry says, so a page is measured with more than a few letters in each. ".repeat(2);
    const heavy = in_month("2026-11", 600).map((e) => ({ ...e, what: longWords }));
    const pages = monthPages([...heavy, ...in_month("2026-10", 50)]);
    expect(pages.length).toBeGreaterThan(2);
    for (const page of pages) {
      const html = await container.renderToString(Month, { props: { page }, params: { month: page.slug }, request: new Request(`https://dmbot.example/whats-new/${page.slug}`) });
      expect(Buffer.byteLength(html) / 1024, page.slug).toBeLessThan(52);
    }
  });
});

describe("the three kinds of page", () => {
  let main: Document;
  let list: Document;
  let mainHtml = "";
  const monthDocs: { slug: string; doc: Document }[] = [];
  beforeAll(async () => {
    const container = await AstroContainer.create();
    mainHtml = await container.renderToString(WhatsNew, { request: new Request("https://dmbot.example/whats-new") });
    main = parsePage(mainHtml);
    list = parsePage(await container.renderToString(Older, { request: new Request("https://dmbot.example/whats-new/older") }));
    for (const page of monthPages(splitEntries(entries, new Date()).older)) {
      const html = await container.renderToString(Month, { props: { page }, params: { month: page.slug }, request: new Request(`https://dmbot.example/whats-new/${page.slug}`) });
      monthDocs.push({ slug: page.slug, doc: parsePage(html) });
    }
  });
  const titles = (doc: Document): string[] => [...doc.querySelectorAll("main .entry h2")].map((h) => h.textContent ?? "");

  it("the main page holds exactly the entries the rule picks", () => {
    expect(titles(main)).toEqual(splitEntries(entries, new Date()).recent.map((e) => e.title));
  });

  it("every visible entry is on exactly one page: the main page or one month's", () => {
    const all = [...titles(main), ...monthDocs.flatMap((m) => titles(m.doc))];
    expect(all).toEqual(entries.map((e) => e.title));
  });

  it("the older page lists a link for each month page, newest first, and no entries", () => {
    const hrefs = [...list.querySelectorAll("main ul a")].map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual(monthDocs.map((m) => `/whats-new/${m.slug}`));
    expect(list.querySelectorAll(".entry")).toHaveLength(0);
  });

  it("each month page is for its own month, kept out of search results, and links back to the list", () => {
    for (const { slug, doc } of monthDocs) {
      expect(doc.querySelector("h1")?.textContent, slug).toContain(monthName(slug.slice(0, 7)));
      expect(doc.querySelector('meta[name="robots"]')?.getAttribute("content"), slug).toBe("noindex");
      expect(doc.querySelector('main a[href="/whats-new/older"]'), slug).toBeTruthy();
    }
  });

  it("the main page links to the older one, and the older one links back", () => {
    if (splitEntries(entries, new Date()).older.length > 0) {
      expect(main.querySelector('main a[href="/whats-new/older"]')?.textContent).toBe(olderHeading);
    }
    expect(list.querySelector('main a[href="/whats-new"]')).toBeTruthy();
  });

  it("the main page leaves room for another entry under the 52 KB budget", () => {
    // (the build measures the real files: this keeps the rule honest; a new entry is about 0.5 KB)
    expect(Buffer.byteLength(mainHtml) / 1024 + 0.6).toBeLessThan(52);
  });
});
