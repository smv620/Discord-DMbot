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

describe("What's new", () => {
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
    expect(held.map((h) => h.title)).toEqual([
      "The name is the button in the NPC list", // until #2425 merges and dev1 deploys it
  },
  {
    date: "2026-10-22",
      "Every recap says which session it is", // until #2418 merges and dev1 deploys it
      "A page for each campaign", // until #2416 merges, its API field is live and dev1 deploys it
      "See where a new name was said", // until #2329 merges and dev1 deploys it
      "Transcripts show real names", // until #2337 merges and dev1 deploys it
      "Three clear ways to approve a recap",
      "Open any NPC and keep your own notes on them", // until #2266 merges and dev1 has seen it
      "Bring your house rules in any file", // until #2377 merges and dev1 deploys it
      "Upload your campaign's backstory from the main menu", // until #2380 merges and dev1 deploys it
      "Recaps are shorter", // until #2368 merges and dev1 deploys it
      "All your names on the website", // until #2294 merges, dev1 deploys it and the Names page (#2295) is live
      "Keep a name from the players until they learn it", // until dev1 deploys it (#2321)
      "Pick the other name from a list", // until #2321 merges and dev1 deploys it
      "DMbot asks when two house rules disagree", // until #2284 merges and dev1 deploys it
      "Hear and record how a name sounds", // until #2316 and #2318 are deployed (#2317)
      "See where your house rules came from", // until #2285 merges and dev1 deploys it
      "See where your homebrew came from", // until #2285 merges and dev1 deploys it
      "Recaps are checked before you see them", // until dev1 deploys it (#2255)
      "Every NPC has its own page", // until dev1 deploys it and the NPC page is live (#2265)
      "Recaps say when they wait", // until #2280 is deployed (#2297)
      "Import reads more transcripts", // until #2302 merges and dev1 deploys it
      "Every page says who you are", // until #2301 is deployed
      "Settings has two small menus", // until #2281 merges and dev1 deploys it
      "See and fix every name online", // until #2294 is deployed (#2295)
      "Support emails show the whole chat", // until #2192 merges and dev1 deploys it
      "DMbot offers how to say a name", // until #2171 merges and dev1 deploys it
      "Your support questions now show the whole conversation", // until #2193 deploys and the helper is on (SUPPORT_AI_LIVE)
      "Make the audio again for a recap you already posted", // until dev1 deploys it (#2203)
      "Recaps for the older sessions you bring in", // until dev1 deploys it (#2106)
      "A recap won't wait forever for your name answers", // until dev1 deploys it (#2174)
      "Say a name your way, from its card", // until #2171 merges and dev1 deploys it
      "Names from your own story", // until dev1 deploys it (#2167)
      "Link a server you added with a plain link", // until dev1 deploys it (#2163)
      "See what your plan is doing", // until the web API change deploys and the owner checks it in test mode (#2165)
      "Names spelled out at the table", // until dev1 deploys it (#2168)
      "Share a recap with a link", // until #2132 and #2142 merge and dev1 deploys them (#2118)
      "A fixed spelling reaches past transcripts", // until dev1 deploys it (#2170)
      "A note when a spelling changed a posted recap", // until dev1 deploys it (#2170, #2178)
      "Your recap waits for your name answers", // until dev1 deploys it (#2169)
      "Bring in older sessions", // until the importer and its page are live (IMPORT_LIVE, #2107)
      "Tell DMbot what to fix", // until the web routes (#2113) are deployed (RECAP_FIX_LIVE, #2114)
      "Speak a note to DMbot", // until the voice routes (#2113 part 2) are deployed (RECAP_VOICE_LIVE, #2114)
      "A short session asks first", // until dev1 deploys it (#2104)
      "Stops listening when no one's there", // until dev1 deploys #2038 (QUIET_STOP_LIVE, #2041)
      "A 📇 Names button", // until dev1 deploys it (#1987, #2002)
      "See what DMbot looked at", // until #1957 deploys (#1958)
      "Look up any rule", // until dev1 deploys it (#1890, #1891)
      "Look up fight difficulty rules", // until dev1 deploys it (#2004, #2006, #2023)
      "See what's switched on", // until dev1 deploys it and the owner's live check (#1831)
      "Find Settings more easily", // until the owner's live check (#1619)
      "Split the party", // until the owner's live check (#1607)
      "Delete a whole campaign", // until the owner's live check (#1608)
      "Add or remove game masters", // until the owner's live check (#1600)
      "Fix a line", // until it has been seen working on the test server (#1491, #1499)
      "New names from your recap", // until dev1 deploys #2030 (RECAP_NAMES_LIVE, #2031)
      "Share a recap by a link", // until the link API is deployed (RECAP_SHARE_LIVE, #2119)
      "Approve the text before any audio", // with narration (RECAP_AUDIO_LIVE, #2084)
      "Narrated recaps with music", // until narration is on (RECAP_AUDIO_LIVE, #1385)
      "Play a recap's voice and music together", // with it (#2017)
      "DMbot's helper answers questions", // until the support helper runs (SUPPORT_AI_LIVE)
      "Ask a question in public", // until QAbot is live (#1352)
      "Move to a new Discord account",
      "New names from what was said",
      "A note before tonight's session",
      "When a player says a secret name",
      "Who is alive, who carries what", // until the extractor is on and it has been seen (#1284)
      "Heads-up when a fallen character speaks", // until dev1 has seen it working (DMBOT_LIVE_HEADS_UP, #2068)
      "Secrets only some people know", // until the extractor is on and it has been seen (#1649)
      "How NPCs feel about the party", // until it has been seen on the test server (#1655)
      "Record what your players did", // until it has been seen on the test server (#1732)
      "Story threads you keep", // until it has been seen on the test server (#1662)
      "Species, subclasses and backgrounds in homebrew", // until seen on the test server (#1663)
      "Keep track of promises made at the table", // until seen on the test server (#1674)
      "A DMbot channel for buttons", // until seen on the test server (#1666)
      "A Shared story switch", // until the story plan is used (#1692, #1714)
      "Guide me: what's next in your adventure", // until 0126 is deployed and seen live (#1731, #1736)
      "Watch the story", // until 0125 is deployed and seen live (#1729, #1735)
      "Link names to your adventure", // until 0124 is deployed and seen live (#1715, #1726)
      "Homebrew menu by kind", // until seen on the test server (#1665)
      "A channel for your house rules", // until seen on the test server (#1752)
      "A channel listing the people and groups", // until seen on the test server (#1761)
      "A time suggestion from your narration", // until the switch is on and seen live (#1758)
      "FightBot follows your turn order", // until dev1 has live-checked it (#1771)
      "Manage your plan without Discord", // until the billing login link is set in the build (#1711)
      "A quiet reminder of the game time", // until seen on the test server (#1738)
      "See how your names are connected", // until #2323 merges and dev1 deploys it
      "Connections on a name's card, one to a line", // until dev1 deploys it (#2323)
      "Change the email DMbot uses", // until #2349 merges and dev1 deploys it
    ]);
    for (const h of held) {
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
