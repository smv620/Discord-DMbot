// The "What DMbot does" section on the home page (#1141): one card for each built feature,
// in plain words, with nothing that isn't built yet.
import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { beforeAll, describe, expect, it } from "vitest";

import { parsePage } from "./dom";

import { features, featuresHeading } from "../src/content/features";
import { INITIATIVE_STYLE_LIVE } from "../src/content/help";
import { RECAPS_LIVE } from "../src/content/recap-words";
import Home from "../src/pages/index.astro";

let home: Document;

beforeAll(async () => {
  const container = await AstroContainer.create();
  home = parsePage(
    await container.renderToString(Home, { request: new Request("https://dmbot.example/") }),
  );
});

describe("What DMbot does", () => {
  it("shows a card for every feature, at most twelve (four rows of three)", () => {
    expect(features.length).toBeLessThanOrEqual(12);
    const cards = [...home.querySelectorAll(".does article")];
    expect(cards.map((c) => c.querySelector("h3")?.textContent)).toEqual(
      features.map((f) => f.title),
    );
    expect(home.querySelector(".does h2")?.textContent).toBe(featuresHeading);
  });

  it("gives each card one sentence and an example", () => {
    for (const f of features) {
      expect(f.what.length).toBeGreaterThan(20);
      expect(f.example.length).toBeGreaterThan(10);
    }
  });

  it("says only what is built, and only rules are shared (#1317)", () => {
    const text = (title: string): string => {
      const f = features.find((x) => x.title.startsWith(title));
      return `${f?.title} ${f?.what} ${f?.example}`;
    };
    const share = text("Share house rules");
    expect(share).toContain("(paid plans)");
    expect(share).not.toMatch(/\bfree\b|anyone can share/i);
    expect(share).toContain("Only the rules are shared, never the story, names or what was said.");
    // Using someone's rules is not paid; only publishing is (PLAN, "Shared rulesets"), so the
    // card says who can share, and it must agree with the pricing page's line.
    expect(share).toContain("A game master on a paid plan can choose to share");
    // Alive-or-dead is stored but nothing records it yet (STORY_MEMORY.md, 5a "what is left"):
    // the story card must not claim it until the extractor ships.
    expect(home.querySelector(".does")?.textContent ?? "").not.toMatch(/alive|\bdead\b|destroyed/i);
    const story = text("Remembers your story");
    expect(story).not.toMatch(/alive|dead|destroyed/i);
    expect(story).toContain("one of a kind");
    expect(story).toContain("who carries them");
    expect(story).toContain("never makes up story");
    expect(story).not.toMatch(/knowledge graph|ontology|claims/i);
  });

  it("promises nothing that isn't built", () => {
    const all = (home.querySelector("main")?.textContent ?? "").toLowerCase();
    for (const notYet of ["typed", "import"]) expect(all).not.toContain(notYet);
  });

  it("shows the recaps and who's who cards, and the initiative sentence, only with their switches (#1816)", () => {
    const titles = features.map((f) => f.title);
    expect(titles.includes("Recaps after each game")).toBe(RECAPS_LIVE);
    expect(titles).not.toContain("A who's who list");
    const fight = features.find((f) => f.title === "FightBot reminders in a fight");
    expect(fight?.what.includes("how your table runs initiative")).toBe(INITIATIVE_STYLE_LIVE);
    expect(titles).toContain("FightBot reminders in a fight");
    expect(titles).toContain("Your own homebrew");
    for (const f of features) expect(f.title.split(" ").length, f.title).toBeLessThanOrEqual(6);
  });

  it("uses no game trademark and no technical words", () => {
    const all = features.map((f) => `${f.title} ${f.what} ${f.example}`).join(" ");
    expect(all).not.toMatch(/D&D|Dungeons|Wizards|Beyond/);
    expect(all).not.toMatch(/knowledge graph|database|API|transcription|LLM/i);
  });
});
