// The "Where to find everything" page (the Help button's 🧭 link): every Discord button and command
// it names is in the bot's own code, DM-only paths are marked, a place whose switch is off is not
// shown, and the page reads as a nested list. The step-by-step is the Help page.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { beforeAll, describe, expect, it } from "vitest";

import { plain } from "../scripts/labels.mjs";
import { parsePage } from "./dom";

import { DM_BOT_CHANNEL_LIVE } from "../src/content/help";
import { commands, discordTree, websiteTree, whereHeading, type Place } from "../src/content/where";
import { PICTURES_LIVE } from "../src/content/pictures-switch";
import { RECAP_STYLE_LIVE } from "../src/content/recap-style-words";
import Where from "../src/pages/where.astro";

const botDir = fileURLToPath(new URL("../../core/src/dmbot/", import.meta.url));

function pyFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? (name === "__pycache__" ? [] : pyFiles(path)) : name.endsWith(".py") ? [path] : [];
  });
}
const botCode = pyFiles(botDir).map((f) => readFileSync(f, "utf8")).join("\n");

function walk(places: Place[], under: Place[] = []): { place: Place; under: Place[] }[] {
  return places.flatMap((p) => [{ place: p, under }, ...walk(p.children ?? [], [...under, p])]);
}

let page: Document;
let main = "";
const squash = (t: string): string => t.replace(/\s+/g, " ").trim();

beforeAll(async () => {
  const container = await AstroContainer.create();
  page = parsePage(await container.renderToString(Where, { request: new Request("https://dmbot.example/where") }));
  main = squash(page.querySelector("main")?.textContent ?? "");
});

describe("the names are the bot's own", () => {
  it("names only buttons and commands that are in the bot's code", () => {
    const missing = walk(discordTree())
      .filter(({ place }) => place.kind === "button" || place.kind === "command")
      .map(({ place }) => (place.kind === "command" ? place.label : plain(place.label)))
      .filter((label) => !botCode.includes(label));
    expect(missing).toEqual([]);
  });

  it("lists every slash command Help lists, and no other", () => {
    const help = readFileSync(join(botDir, "ui/logic.py"), "utf8");
    const inHelp = [...help.matchAll(/\(\s*"(\/[a-z -]+)",\s*"[^"]*",\s*(?:True|False),?\s*\)/g)].map((m) => m[1]).sort();
    expect(commands.map((c) => c.label).sort()).toEqual(inHelp);
  });

  it("marks the same commands as DM only that Help does", () => {
    const help = readFileSync(join(botDir, "ui/logic.py"), "utf8");
    const dmOnly = [...help.matchAll(/\(\s*"(\/[a-z -]+)",\s*"[^"]*",\s*True,?\s*\)/g)].map((m) => m[1]).sort();
    expect(commands.filter((c) => c.dmOnly).map((c) => c.label).sort()).toEqual(dmOnly);
  });
});

describe("DM-only paths are marked", () => {
  it("marks Manage DMbot and Manage campaign, and everything under them is the DM's", () => {
    const found = walk(discordTree());
    const manage = found.filter(({ place }) => place.label === "Manage DMbot");
    expect(manage.length).toBeGreaterThan(0);
    for (const { place } of manage) expect(place.dmOnly).toBe(true);
    if (DM_BOT_CHANNEL_LIVE) expect(found.some(({ place }) => place.label === "Manage campaign" && place.dmOnly)).toBe(true);
  });

  it("shows the badge in the page for each DM-only place", () => {
    const badges = page.querySelectorAll(".badge").length;
    const marked = [...walk(discordTree()), ...walk(websiteTree())].filter(({ place }) => place.dmOnly).length;
    expect(badges).toBe(marked);
    expect(badges).toBeGreaterThan(10);
  });

  it("marks the DM's website pages, from the same list the campaign tabs use", () => {
    const labels = (flag: boolean): string[] => walk(websiteTree()).filter(({ place }) => !!place.dmOnly === flag).map(({ place }) => place.label);
    expect(labels(true)).toContain("Names and NPCs");
    expect(labels(false)).toContain("Transcripts");
    expect(labels(false)).not.toContain("Names and NPCs");
  });
});

describe("only what is live is shown", () => {
  it("leaves out a page whose switch is off", () => {
    const labels = walk(websiteTree()).map(({ place }) => place.label);
    expect(labels.includes("Recap style")).toBe(RECAP_STYLE_LIVE);
    expect(labels.includes("Pictures")).toBe(PICTURES_LIVE);
  });

  it("starts from the DM screen until the DMbot channel is live, and from the channel after", () => {
    const labels = discordTree().map((p) => p.label);
    expect(labels.includes("The DM Bot channel")).toBe(DM_BOT_CHANNEL_LIVE);
    expect(labels.includes("Your DM screen")).toBe(!DM_BOT_CHANNEL_LIVE);
  });
});

describe("the page", () => {
  it("has one heading, and a section for Discord and one for the website", () => {
    expect(page.querySelectorAll("h1").length).toBe(1);
    expect(page.querySelector("h1")?.textContent).toBe(whereHeading);
    expect([...page.querySelectorAll("main h2")].map((h) => h.textContent)).toEqual(["In Discord", "On the website"]);
  });

  it("is a nested list, and says what DM only means", () => {
    expect(page.querySelectorAll("main ul ul").length).toBeGreaterThan(5);
    expect(main).toContain("DM only means only a campaign's game masters can use it. Everything inside a DM only place is DM only too.");
  });

  it("links to Help, and shows a path with a placeholder as text, not a link", () => {
    const hrefs = [...page.querySelectorAll("main a")].map((a) => a.getAttribute("href"));
    expect(hrefs).toContain("/help");
    expect(hrefs.some((h) => h?.includes("{") || h?.includes("%7B"))).toBe(false);
  });

  it("is in the footer", () => {
    expect([...page.querySelectorAll("footer a")].map((a) => a.getAttribute("href"))).toContain("/where");
  });
});
