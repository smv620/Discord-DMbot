// The What's new page (#1321): newest first, short, plain, and every button it names is one the
// bot really shows. Only what is live goes here (docs/testing-status.log, "LIVE NOW").
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { beforeAll, describe, expect, it } from "vitest";

import { parsePage } from "./dom";

import { entries, formatDate, held, whatsNewHeading } from "../src/content/whats-new";
import Help from "../src/pages/help.astro";
import Home from "../src/pages/index.astro";
import WhatsNew from "../src/pages/whats-new.astro";

const botDir = fileURLToPath(new URL("../../core/src/dmbot/", import.meta.url));
let page: Document;
let help: Document;
let home: Document;

beforeAll(async () => {
  const container = await AstroContainer.create();
  const render = async (component: Parameters<typeof container.renderToString>[0], path: string): Promise<Document> =>
    parsePage(await container.renderToString(component, { request: new Request(`https://dmbot.example${path}`) }));
  page = await render(WhatsNew, "/whats-new");
  help = await render(Help, "/help");
  home = await render(Home, "/");
});

// The live list as it read when the entries moved into one file each (#2468), newest first. Later
// entries may appear anywhere; these must stay, in this order.
const liveWhenSplit = [
  "Recaps for your table",
  "Recaps fit your session",
  "Recaps stick to what happened",
  "Choose what recaps do",
  "When you say a secret name",
  "My Account puts sign-in first",
  "Campaign labels look like labels",
  "A shorter campaigns list",
  "Search and link Help steps",
  "DMbot works on any new server",
  "Add homebrew from a link",
  "Share imported homebrew in a pack",
  "See the sites you approved",
  "Which rules is a book for?",
  "Check names after a session",
  "Keep a connection secret",
  "Homebrew for your campaign",
  "Copy a pack's entry to edit",
  "Players can look up rules",
  "Keep transcripts for a set time",
  "Delete my transcripts",
  "See how long transcripts are kept",
  "Deleted transcripts show in the list",
  "Read shared homebrew on the website",
  "Read house rules on the website",
  "Read transcripts on the website",
  "Your questions to the team",
  "Quick answers use your facts",
  "Check what DMbot noticed",
  "Fight reminders for players",
  "Stop recording me on the website",
  "Sign in with an emailed link",
  "Your DMbot ID",
  "Share house rules with other tables",
  "Suggest a rule back",
  "One-of-a-kind items",
  "Ask DMbot by typing",
  "Link your character sheet",
  "See when your plan ends",
  "The game clock",
];

describe("What's new", () => {
  it("keeps the live list as it was when the entries moved into files (#2468)", () => {
    const now = entries.map((e) => e.title);
    expect(now.filter((t) => liveWhenSplit.includes(t))).toEqual(liveWhenSplit);
    expect(page.querySelectorAll(".entry").length).toBe(entries.length);
  });

  it("gives every file the same shape, with a name that starts with its date (#2468)", () => {
    const names = Object.keys(import.meta.glob("../src/content/whats-new/*.ts"));
    expect(names.length).toBe(entries.length + held.length);
    const titles = [...entries, ...held].map((e) => e.title);
    expect(new Set(titles).size, "two files share a title").toBe(titles.length);
    for (const name of names) {
      const file = name.split("/").pop() ?? "";
      const e = [...entries, ...held].find((x) => file.startsWith(x.date));
      expect(e, `${file} starts with a date some entry has`).toBeDefined();
      expect(file, file).toMatch(/^\d{4}-\d{2}-\d{2}-[a-z0-9-]+\.ts$/);
    }
  });

  it("says style notes are kept for this campaign's later recaps and where to remove them", () => {
    const fix = [...entries, ...held].find((e) => e.title === "Tell DMbot what to fix");
    expect(fix?.what).toContain("this campaign's later recaps");
    expect(fix?.what).toContain("newest 10");
    expect(fix?.what).toContain("⚙️ Settings → 📜 Recaps → ✍️ Recap style");
  });

  it("lists every entry, newest first, with the day it went live", () => {
    expect(page.querySelector("h1")?.textContent).toBe(whatsNewHeading);
    const shown = [...page.querySelectorAll(".entry")];
    expect(shown.map((e) => e.querySelector("h2")?.textContent)).toEqual(entries.map((e) => e.title));
    expect(shown.map((e) => e.querySelector("time")?.getAttribute("datetime"))).toEqual(
      entries.map((e) => e.date),
    );
  });

  it("shows the two things dev1 deployed as 4d7aea9b (#1373, #1370)", () => {
    const titles = entries.map((e) => e.title);
    expect(titles).toEqual(
      expect.arrayContaining(["Your questions to the team", "Quick answers use your facts"]),
    );
  });

  it("shows what Test J saw working on the live bot (#1532, #1533, #1527), and the rule lookups (#1552)", () => {
    const titles = entries.map((e) => e.title);
    for (const t of [
      "Delete my transcripts",
      "Keep transcripts for a set time",
      "Homebrew for your campaign",
      "Copy a pack's entry to edit",
      "Players can look up rules",
    ])
      expect(titles, t).toContain(t);
    expect(held.map((h) => h.title)).not.toContain("Delete my past transcripts");
    expect(held.map((h) => h.title)).not.toContain("Your own homebrew");
  });

  it("holds back what people can't use yet, with the reason, and shows none of it (#1322)", () => {
    expect(held.length).toBeGreaterThan(0);
    for (const h of held) {
      expect(h.reason.trim(), h.title).not.toBe("");
      expect(h.reason.length, h.title).toBeGreaterThan(20);
      expect(entries.map((e) => e.title)).not.toContain(h.title);
    }
    const shown = page.querySelector("main")?.textContent ?? "";
    expect(shown).not.toContain("Move to a new Discord account");
    expect(shown).not.toContain("New names from what was said");
    expect(shown).not.toContain("A note before tonight");
    expect(shown).not.toContain("When a player says a secret name");
    expect(shown).not.toContain("move code");
    expect(shown).not.toContain("Ask a question in public"); // not until QAbot is live
    expect(shown).not.toContain("QAbot");
    expect(page.querySelectorAll(".entry")).toHaveLength(entries.length);
    expect(entries.length).toBeGreaterThanOrEqual(10); // each live entry is shown (#1521)
  });

  it("has dates in order, newest first, and real days", () => {
    expect(entries.length).toBeGreaterThanOrEqual(5);
    for (const [i, e] of entries.entries()) {
      expect(e.date, e.title).toMatch(/^\d{4}-\d{2}-\d{2}$/);
      // Round-trip, because "2026-02-31" parses (as 3 March) and would pass a plain parse.
      expect(new Date(e.date).toISOString().slice(0, 10), e.title).toBe(e.date);
      if (i > 0) expect(e.date <= (entries[i - 1]?.date ?? ""), `${e.title} is newer than the one above it`).toBe(true);
    }
  });

  it("writes the date in words that read the same everywhere, and refuses a bad one", () => {
    expect(formatDate("2026-10-15")).toBe("15 October 2026");
    expect(formatDate("2026-01-03")).toBe("3 January 2026");
    expect(() => formatDate("2026-13-01")).toThrow("Not a date");
    expect(() => formatDate("x")).toThrow("Not a date");
  });

  it("keeps each title to six words and each entry to one or two sentences", () => {
    for (const e of entries) {
      expect(e.title.split(/\s+/).length, e.title).toBeLessThanOrEqual(6);
      // A sentence ends at . ! or ? followed by a space or the end; keep abbreviations ("e.g.")
      // and decimals out of the entries, or this counts them as extra sentences.
      const sentences = e.what.split(/[.!?](?:\s|$)/).filter((s) => s.trim() !== "");
      expect(sentences.length, e.title).toBeGreaterThanOrEqual(1);
      expect(sentences.length, e.title).toBeLessThanOrEqual(2);
    }
  });

  it("claims nothing that isn't live, and uses no hard-rule banned term", () => {
    const all = [...entries, ...held].map((e) => `${e.title} ${e.what}`).join(" ");
    // Alive-or-dead is stored but nothing records it until the extractor is on (#1284), and
    // who-carries-what likewise (#1319; docs/STORY_MEMORY.md, 5a): they wait for their own entry,
    // which stays held (help.test.ts ties it to STORY_STATES_LIVE), so nothing else may say them.
    const others = [...entries, ...held]
      .filter((e) => e.title !== "Who is alive, who carries what")
      .map((e) => `${e.title} ${e.what}`)
      .join(" ");
    expect(others).not.toMatch(/alive|\bdead\b|destroyed|carries|carried/i);
    expect(all).not.toMatch(/knowledge graph|ontology|claims|extractor/i);
    expect(all).not.toMatch(/D&D|Dungeons|Wizards|Beyond/);
    expect(all).not.toMatch(/database|API|LLM|transcription/i);
  });

  it("names only buttons and commands that are in the bot's code", () => {
    const named: [needle: string, file: string][] = [
      ["Get a move code", "ui/migrate.py"],
      ["/dmbot migrate", "ui/migrate.py"],
      ["Import my account", "ui/migrate.py"],
      ["Only one exists", "ui/name_card.py"],
      ["Allow more than one", "ui/name_card.py"],
      ["dmb-chat", "dm_screen/dmbchat_channel.py"],
    ];
    const all = [...entries, ...held].map((e) => e.what).join(" ");
    for (const [needle, file] of named) {
      expect(all, `${needle} is in an entry`).toContain(needle);
      expect(readFileSync(join(botDir, file), "utf8"), `${needle} in ${file}`).toContain(needle);
    }
  });

  it("says the same as the Help page and the account page about the emailed link and the move code", () => {
    const text = (title: string): string =>
      [...entries, ...held].find((e) => e.title.startsWith(title))?.what ?? "";
    expect(text("Sign in")).toContain("works once and stops working after 15 minutes");
    expect(text("Move to")).toContain("emailed code");
    expect(text("Move to")).toContain("say yes to being recorded again"); // account.ts moveSent
    expect(text("Share house rules")).toContain("paid plan");
    expect(text("Suggest")).toContain("It goes with no name");
    expect(text("Suggest")).toContain("nothing changes until that game master adds it");
  });

  it("is linked from the footer and from the top of the Help page", () => {
    const footer = [...home.querySelectorAll("footer a")].map((a) => a.getAttribute("href"));
    expect(footer).toContain("/whats-new");
    const link = help.querySelector('main a[href="/whats-new"]');
    expect(link).not.toBeNull();
    // At the top: before the first section heading.
    const firstHeading = help.querySelector("main h2");
    expect(link && firstHeading && link.compareDocumentPosition(firstHeading) & 4).toBeTruthy(); // 4: the heading follows
  });
});
