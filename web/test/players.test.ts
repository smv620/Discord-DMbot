// The /players page (#1821): a short page a game master can send to the table. It uses the Help
// page's own player steps (one copy each) and shows a feature only while its switch is on.
import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { beforeAll, describe, expect, it } from "vitest";

import { parsePage } from "./dom";

import {
  INITIATIVE_STYLE_LIVE,
  consentStep,
  dmSteps,
  fightBotStep,
  heyDmBotStep,
  initiativeStyleSteps,
  noThanksStep,
  playerNote,
  stopStep,
  transcriptStep,
} from "../src/content/help";
import { playersHeading } from "../src/content/players";
import Players from "../src/pages/players.astro";

let page: Document;
let main = "";
const squash = (t: string): string => t.replace(/\s+/g, " ").trim();

beforeAll(async () => {
  const container = await AstroContainer.create();
  page = parsePage(await container.renderToString(Players, { request: new Request("https://dmbot.example/players") }));
  main = squash(page.querySelector("main")?.textContent ?? "");
});

describe("the players page", () => {
  it("has one heading, and a short page of three sections", () => {
    expect(page.querySelectorAll("h1").length).toBe(1);
    expect(page.querySelector("h1")?.textContent).toBe(playersHeading);
    expect([...page.querySelectorAll("main h2")].map((h) => h.textContent)).toEqual(["What you agree to", "Saying yes", "What you might see"]);
    // The consent promises (#1882) are on this page too, so it is a little longer than the first version.
    expect(main.split(" ").length).toBeLessThan(500);
  });

  it("shows the Help page's own player steps, word for word", () => {
    for (const step of [consentStep, noThanksStep, stopStep, transcriptStep, heyDmBotStep, fightBotStep]) {
      expect(main).toContain(squash(step.text));
    }
    expect(main).toContain(squash(playerNote));
  });

  it("shows a feature only while its switch is on (#1821)", () => {
    for (const step of initiativeStyleSteps) expect(main.includes(squash(step.text))).toBe(INITIATIVE_STYLE_LIVE);
  });

  it("links to the Help player steps and the privacy page", () => {
    const hrefs = [...page.querySelectorAll("main a")].map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual(["/help#player-heading", "/legal/privacy"]);
  });

  it("is linked from the footer, and from the game master's first Help step", () => {
    expect([...page.querySelectorAll("footer a")].map((a) => a.getAttribute("href"))).toContain("/players");
    expect(dmSteps[0]?.text).toContain("Send your players to getdmbot.com/players.");
  });
});
