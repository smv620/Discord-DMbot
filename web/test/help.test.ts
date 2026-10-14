// The Help page (#1142): every button and command it names is one the bot really shows, so
// the page can't drift from core/src/dmbot.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { beforeAll, describe, expect, it } from "vitest";

import { parsePage } from "./dom";

import { accountSteps, dmSteps, playerSteps } from "../src/content/help";
import Help from "../src/pages/help.astro";

const botDir = fileURLToPath(new URL("../../core/src/dmbot/", import.meta.url));
let help: Document;

beforeAll(async () => {
  const container = await AstroContainer.create();
  help = parsePage(
    await container.renderToString(Help, { request: new Request("https://dmbot.example/help") }),
  );
});

describe("Help", () => {
  it("has a game master section and a player section", () => {
    expect(help.querySelector("#dm-heading")?.textContent).toBe("For the game master");
    expect(help.querySelector("#player-heading")?.textContent).toBe("For players");
    const lists = [...help.querySelectorAll("section ol")];
    expect(lists.map((l) => l.children.length)).toEqual([dmSteps.length, playerSteps.length, accountSteps.length]);
  });

  it("has a Your account section with Discord's own reset and the account page (#1215)", () => {
    expect(help.querySelector("#account-heading")?.textContent).toBe("Your account");
    const links = [...help.querySelectorAll("#account-heading ~ ol a")].map((a) => a.getAttribute("href"));
    expect(links).toEqual(["https://discord.com/login", "/account"]);
    const section = help.querySelector("#account-heading")?.parentElement?.textContent ?? "";
    expect(section).toContain("Forgot your password?");
    expect(section).toContain("DMbot never has your password.");
  });

  it("names only buttons and commands that are in the bot's code", () => {
    for (const step of [...dmSteps, ...playerSteps, ...accountSteps]) {
      for (const { name, file } of step.names ?? []) {
        expect(step.text, `${name} is in its step`).toContain(name);
        const source = readFileSync(join(botDir, file), "utf8");
        expect(source, `${name} in ${file}`).toContain(name);
      }
    }
  });

  it("links to Add to Discord and Say hello", () => {
    const links = [...help.querySelectorAll("main a")].map((a) => a.getAttribute("href"));
    expect(links).toContain("/install");
    expect(links).toContain("/hello");
  });

  it("is in the menu and the footer", () => {
    const hrefs = [...help.querySelectorAll("nav a, footer a")].map((a) => a.getAttribute("href"));
    expect(hrefs.filter((h) => h === "/help")).toHaveLength(2);
  });
});
