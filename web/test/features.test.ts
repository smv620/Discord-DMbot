// The "What DMbot does" section on the home page (#1141): one card for each built feature,
// in plain words, with nothing that isn't built yet.
import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { beforeAll, describe, expect, it } from "vitest";

import { parsePage } from "./dom";

import { features, featuresHeading } from "../src/content/features";
import Home from "../src/pages/index.astro";

let home: Document;

beforeAll(async () => {
  const container = await AstroContainer.create();
  home = parsePage(
    await container.renderToString(Home, { request: new Request("https://dmbot.example/") }),
  );
});

describe("What DMbot does", () => {
  it("shows a card for every feature, at most eight", () => {
    expect(features.length).toBeLessThanOrEqual(8);
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

  it("promises nothing that isn't built", () => {
    const all = (home.querySelector("main")?.textContent ?? "").toLowerCase();
    for (const notYet of ["fightbot", "typed", "import"]) expect(all).not.toContain(notYet);
  });

  it("uses no game trademark and no technical words", () => {
    const all = features.map((f) => `${f.title} ${f.what} ${f.example}`).join(" ");
    expect(all).not.toMatch(/D&D|Dungeons|Wizards|Beyond/);
    expect(all).not.toMatch(/knowledge graph|database|API|transcription|LLM/i);
  });
});
