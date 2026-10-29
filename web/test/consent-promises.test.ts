// The consent promises (#1882): /players and Help's player section show the one list in
// src/content/consent-promises.ts, and that list says what CLAUDE.md's hard rules say players agree to.
import { readFileSync } from "node:fs";
import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { beforeAll, describe, expect, it } from "vitest";

import { parsePage } from "./dom";

import { consentPromises, consentPromisesHeading, consentPromisesLead } from "../src/content/consent-promises";
import Help from "../src/pages/help.astro";
import Players from "../src/pages/players.astro";

const squash = (t: string): string => t.replace(/\s+/g, " ").trim();
const pages: Record<string, string> = {};

beforeAll(async () => {
  const container = await AstroContainer.create();
  for (const [name, component, path] of [
    ["players", Players, "/players"],
    ["help", Help, "/help"],
  ] as const) {
    const page = parsePage(await container.renderToString(component, { request: new Request(`https://dmbot.example${path}`) }));
    pages[name] = squash(page.querySelector("main")?.textContent ?? "");
  }
});

describe("the consent promises", () => {
  it("say what the hard rules say players agree to", () => {
    const all = consentPromises.map((p) => p.text).join(" ");
    expect(consentPromises.map((p) => p.id)).toEqual(["only-yes", "questions", "ai", "cloud", "transcript", "stop", "dm-screen"]);
    expect(all).toContain("records only people who say yes");
    expect(all).toContain("reads what you type");
    expect(all).toContain("not used to train their AI");
    expect(all).toContain("never records another bot");
    expect(all).toContain("Everyone in your Discord server can read and download");
    expect(all).toContain("your name and the question are posted there for everyone to read");
    expect(all).toContain("another company turns your voice into text");
    expect(all).toContain("Yes, stop recording me");
    expect(all).toContain("never sends the game master's private notes to players");
  });

  for (const name of ["players", "help"]) {
    it(`leaves no promise out of the ${name} page`, () => {
      expect(pages[name]).toContain(squash(consentPromisesLead));
      for (const promise of consentPromises) expect(pages[name], promise.id).toContain(squash(promise.text));
    });
  }

  it("has a heading on the players page", () => {
    expect(pages["players"]).toContain(consentPromisesHeading);
  });

  it("carries the terms version the bot is on", () => {
    const file = readFileSync(new URL("../src/content/consent-promises.ts", import.meta.url), "utf8");
    expect(file).toMatch(/^\/\/ TERMS_VERSION: \d+$/m);
  });
});
