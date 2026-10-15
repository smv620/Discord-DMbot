// "Before you start" on the home page (#1324): short, plain answers, no new privacy wording and
// no prices, a link to a real page where an answer needs more, one h1 on the page.
import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { beforeAll, describe, expect, it } from "vitest";

import { parsePage } from "./dom";

import { beforeHeading, beforeQuestions } from "../src/content/before-you-start";
import { publicPaths } from "../src/content/site";
import Home from "../src/pages/index.astro";

let home: Document;

beforeAll(async () => {
  const container = await AstroContainer.create();
  home = parsePage(
    await container.renderToString(Home, { request: new Request("https://dmbot.example/") }),
  );
});

describe("Before you start", () => {
  it("has the six questions, as open-and-close rows under its own heading, after the feature cards", () => {
    const section = home.querySelector(".before");
    expect(section?.querySelector("h2")?.textContent).toBe(beforeHeading);
    const asked = [...(section?.querySelectorAll("details > summary") ?? [])].map((s) => s.textContent);
    expect(asked).toEqual(beforeQuestions.map((q) => q.question));
    expect(asked).toHaveLength(6);
    const does = home.querySelector(".does");
    expect(does && section && does.compareDocumentPosition(section) & 4).toBeTruthy(); // 4: after
  });

  it("keeps exactly one h1 on the page", () => {
    expect(home.querySelectorAll("h1")).toHaveLength(1);
  });

  it("answers in one to three sentences each", () => {
    for (const q of beforeQuestions) {
      const sentences = q.answer.split(/[.!?](?:\s|$)/).filter((s) => s.trim() !== "");
      expect(sentences.length, q.question).toBeGreaterThanOrEqual(1);
      expect(sentences.length, q.question).toBeLessThanOrEqual(3);
    }
  });

  it("uses none of the banned technical terms, no trademark and no price", () => {
    const all = beforeQuestions.map((q) => `${q.question} ${q.answer} ${q.more?.text ?? ""}`).join(" ");
    expect(all).not.toMatch(/knowledge graph|ontology|claims|extractor|database|API\b|LLM|transcription/i);
    expect(all).not.toMatch(/D&D|Dungeons|Wizards|Beyond|sourcebook/i);
    expect(all).not.toMatch(/[$€£]\s?\d|\d\s?(USD|dollars)/i); // prices live on /pricing only
  });

  it("promises nothing the privacy page doesn't (it links there instead)", () => {
    const text = (start: string): string => beforeQuestions.find((q) => q.question.startsWith(start))?.answer ?? "";
    expect(text("Who can read")).toContain("Everyone in your Discord server can read and download");
    expect(beforeQuestions.find((q) => q.question.startsWith("Who can read"))?.more?.href).toBe("/legal/privacy");
    // The three notes settings, in the words of dm_screen/messages.py; "peek" is the default.
    expect(text("Can my players")).toContain("players can peek after a spoiler warning");
    expect(text("Does DMbot make rulings")).toContain("the game master decides");
    expect(text("Does DMbot make rulings")).toContain("never makes up story");
  });

  it("links only to pages the site has, and the plans question links to /pricing", () => {
    const hrefs = beforeQuestions.flatMap((q) => (q.more ? [q.more.href] : []));
    for (const href of hrefs) expect([...publicPaths], href).toContain(href);
    expect(beforeQuestions.find((q) => q.question === "What do I need?")?.more?.href).toBe("/pricing");
    const shown = [...home.querySelectorAll(".before a")].map((a) => a.getAttribute("href"));
    expect(shown).toEqual(hrefs);
  });
});
