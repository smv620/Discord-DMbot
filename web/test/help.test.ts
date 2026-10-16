// The Help page (#1142): every button and command it names is one the bot really shows, so
// the page can't drift from core/src/dmbot.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { beforeAll, describe, expect, it } from "vitest";

import { parsePage } from "./dom";

import { accountSteps, dmSteps, emailSteps, quickAnswers, quickAnswersHeading, gettingHelp, playerSteps } from "../src/content/help";
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
    expect(lists.map((l) => l.children.length)).toEqual([dmSteps.length, playerSteps.length, accountSteps.length, emailSteps.length, gettingHelp.items.length]);
  });

  it("says what DMbot knows when the game master asks it a quick question (#1379)", () => {
    expect(help.querySelector("#quick-heading")?.textContent).toBe(quickAnswersHeading);
    const items = [...help.querySelectorAll("#quick-heading ~ ul li")].map((li) => li.textContent);
    expect(items).toEqual(quickAnswers.map((s) => s.text));
    const all = items.join(" ");
    expect(all).toContain("the story facts you have confirmed about the names in your question");
    expect(all).toContain("your own notes on names and places");
    expect(all).toContain("names you marked secret");
    expect(all).toContain("I don't have that. Your call.");
    expect(all).toContain("posted there for everyone");
    expect(all).toContain("Everyone in your Discord server can read that record");
    expect(all).not.toMatch(/knowledge graph|recall|prompt|transcript|AI model/i); // plain words
  });

  it("has a Your account section with Discord's own reset and the account page (#1215)", () => {
    expect(help.querySelector("#account-heading")?.textContent).toBe("Your account");
    const links = [...help.querySelectorAll("#account-heading ~ ol a")].map((a) => a.getAttribute("href"));
    expect(links).toEqual(["https://discord.com/login", "/account"]);
    const section = help.querySelector("#account-heading")?.parentElement?.textContent ?? "";
    expect(section).toContain("Forgot your password?");
    expect(section).toContain("DMbot has no password.");
  });

  it("names only buttons and commands that are in the bot's code", () => {
    for (const step of [...dmSteps, ...quickAnswers, ...playerSteps, ...accountSteps, ...emailSteps]) {
      for (const { name, file } of step.names ?? []) {
        expect(step.text, `${name} is in its step`).toContain(name);
        const source = readFileSync(join(botDir, file), "utf8");
        expect(source, `${name} in ${file}`).toContain(name);
      }
    }
  });

  it("says what each email is, and that a move code is only for you (#1283)", () => {
    const section = help.querySelector("#emails-heading")?.parentElement?.textContent ?? "";
    expect(section).toContain("A move code is only for you: never give it to anyone.");
    expect(section).toContain("Ignore the email. Nothing changes.");
    expect(section).toContain("stops working after 15 minutes");
    expect(section).toContain("lasts 30 minutes");
    expect(section).not.toMatch(/@|\d+ [A-Z][a-z]+ (Street|St|Road|Ave)/); // no address on the site
  });

  it("calls the update note's button Undo, as the note itself does (#1254)", () => {
    const source = readFileSync(join(botDir, "dm_screen/set_update.py"), "utf8");
    expect(source).toContain('"undo": ("Undo"');
    expect(dmSteps.map((s) => s.text).join(" ")).not.toContain("↩️ Undo");
  });

  it("tells the suggesting game master and the publisher what DMbot really shows (#1313)", () => {
    const steps = dmSteps.map((s) => s.text);
    const suggest = steps.find((t) => t.includes("💡 Suggest")) ?? "";
    expect(suggest).toContain("with no name on it");
    expect(steps.join(" ")).toContain("You hear the answer on your DM screen at your next session.");
    // The note's buttons are See, Add to set and No thanks, not "Accept" (set_suggestion.py).
    const note = steps.find((t) => t.includes("Add to set")) ?? "";
    expect(note).toContain("It never says which table sent it.");
    expect(note).toContain("Nothing changes until you press Add to set.");
    expect(note).not.toContain("Accept");
    const source = readFileSync(join(botDir, "dm_screen/set_suggestion.py"), "utf8");
    for (const label of ['"see": ("See"', '"accept": ("Add to set"', '"no": ("No thanks"']) {
      expect(source, label).toContain(label);
    }
    expect(steps.join(" ")).toContain("This copy no longer gets updates.");
    expect(steps.join(" ")).toContain("It does not keep the share code.");
    // A table that still follows a set sees only Stop using shared rules, and following again is
    // refused until then (shared_rules.py SharedMenu, rule_sets.py ALREADY_FOLLOWING), so both
    // ways back to updates say to press Stop using shared rules first.
    for (const marker of ["stop being shared", "A backup keeps"]) {
      const step = steps.find((t) => t.includes(marker)) ?? "";
      expect(step.indexOf("Stop using shared rules"), marker).toBeGreaterThan(-1);
      expect(step.indexOf("Stop using shared rules"), marker).toBeLessThan(step.indexOf("📥 Use shared rules"));
    }
  });

  it("links to Add to Discord and Say hello", () => {
    const links = [...help.querySelectorAll("main a")].map((a) => a.getAttribute("href"));
    expect(links).toContain("/install");
    expect(links).toContain("/hello");
  });

  it("is in the menu and the footer", () => {
    const hrefs = [...help.querySelectorAll("nav a, footer a")].map((a) => a.getAttribute("href"));
    expect(hrefs.filter((h) => h === "/help")).toHaveLength(1); // the menu
    expect(hrefs.filter((h) => h === "/help#getting-help")).toHaveLength(1); // the footer (#1352)
  });
});
