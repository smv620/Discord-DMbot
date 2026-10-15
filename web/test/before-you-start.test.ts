// "Before you start" on the home page (#1324): short, plain answers, no new privacy wording and
// no prices, a link to a real page where an answer needs more, one h1 on the page.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

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
  it("has the seven questions, as open-and-close rows under its own heading, after the feature cards", () => {
    const section = home.querySelector(".before");
    expect(section?.querySelector("h2")?.textContent).toBe(beforeHeading);
    const asked = [...(section?.querySelectorAll("details > summary") ?? [])].map((s) => s.textContent);
    expect(asked).toEqual(beforeQuestions.map((q) => q.question));
    // Pinned, so a renamed or dropped question fails here and not only against itself.
    expect(asked).toEqual([
      "Does it record everyone?",
      "Can my players see my DM notes?",
      "Will DMbot take over my game?",
      "Who can read the game's written record?",
      "Does it send what we say to an AI?",
      "Which rules does it use?",
      "What do I need?",
    ]);
    const does = home.querySelector(".does");
    expect(does && section && does.compareDocumentPosition(section) & 4).toBeTruthy(); // 4: after
  });

  it("names only companies the privacy page and the yes message already name (#1326)", () => {
    const row = beforeQuestions.find((q) => q.question.startsWith("Does it send"));
    expect(row?.more?.href).toBe("/legal/privacy");
    const privacy = readFileSync(fileURLToPath(new URL("../src/pages/legal/privacy.astro", import.meta.url)), "utf8");
    const consent = readFileSync(fileURLToPath(new URL("../../core/src/dmbot/consent_dm.py", import.meta.url)), "utf8");
    // Every capitalised company in the answer: the ones it names are on the privacy page, and the AI
    // company is the one the yes message names too.
    for (const company of ["Deepgram", "Anthropic"]) {
      expect(row?.answer, company).toContain(company);
      expect(privacy, `${company} on the privacy page`).toContain(company);
    }
    expect(consent, "the yes message and the privacy page must stay in sync").toContain("AI company (Anthropic)");
    expect(row?.answer).toContain("thrown away");
    expect(privacy).toContain("the sound is thrown away");
    // "It gets the text it needs, not sound" is the privacy page's own phrase (across a line break).
    expect(privacy.replace(/\s+/g, " ")).toContain("It gets the text it needs, not sound");
    expect(row?.answer).toContain("gets the text it needs, not sound");
    // The yes message says the text isn't used to train the AI; the privacy page doesn't, so the
    // answer must not either until it does.
    expect(row?.answer).not.toMatch(/train/i);
    // Any capitalised word, short ones and all-caps ones too ("OVH", "AWS"), apart from the few
    // plain words that start a sentence or say what it is.
    const names = (row?.answer.match(/\b[A-Z][A-Za-z]+\b/g) ?? []).filter((w) => !["Yes", "AI", "An", "A"].includes(w));
    expect(names.sort()).toEqual(["Anthropic", "Deepgram"]);
  });

  it("opens the first row, so the recording answer is read without a click", () => {
    const rows = [...home.querySelectorAll(".before details")];
    expect(rows[0]?.hasAttribute("open")).toBe(true);
    expect(rows.slice(1).some((r) => r.hasAttribute("open"))).toBe(false);
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
    expect(all).not.toMatch(/[$€£]\s?\d|\d\s?(USD|dollars)|\ba month\b|\bper month\b|\bfree\b|\bpaid plan/i); // prices live on /pricing only
  });

  it("promises nothing the privacy page doesn't (it links there instead)", () => {
    const text = (start: string): string => beforeQuestions.find((q) => q.question.startsWith(start))?.answer ?? "";
    expect(text("Who can read")).toContain("Everyone in your Discord server can read and download it");
    expect(beforeQuestions.find((q) => q.question.startsWith("Who can read"))?.more?.href).toBe("/legal/privacy");
    // The three notes settings, in the words of dm_screen/messages.py; "peek" is the default.
    expect(text("Can my players")).toContain("players can peek after a spoiler warning (the usual setting");
    expect(text("Can my players")).toContain("Server owners and admins can always see every channel");
    expect(text("Does it record")).toContain("16 or older");
    expect(text("Does it record")).toContain("what was already written stays in the record");
    expect(text("Will DMbot")).toContain("you decide");
    expect(text("Will DMbot")).toContain("never makes up story");
    expect(text("Who can read")).toContain("stays there even if they stop later");
    // Not "only people who said yes are in it": the DM sidebar's and table questions go in too.
    expect(text("Who can read")).not.toMatch(/only people who said yes/i);
  });

  it("links only to pages the site has, and the plans question links to /pricing", () => {
    const hrefs = beforeQuestions.flatMap((q) => (q.more ? [q.more.href] : []));
    for (const href of hrefs) expect([...publicPaths], href).toContain(href);
    expect(beforeQuestions.find((q) => q.question === "What do I need?")?.more?.href).toBe("/pricing");
    const shown = [...home.querySelectorAll(".before a")].map((a) => a.getAttribute("href"));
    expect(shown).toEqual(hrefs);
  });
});
