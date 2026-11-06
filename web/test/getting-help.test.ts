// The "Getting help" section of the Help page and its footer link (#1352): where to ask, in
// order, with one support address and the public Q&A link, and no new privacy promises.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { beforeAll, describe, expect, it } from "vitest";

import { parsePage } from "./dom";

import { QA_URL } from "../src/content/account";
import { gettingHelp, SUPPORT_EMAIL } from "../src/content/help";
import { held } from "../src/content/whats-new";
import Help from "../src/pages/help.astro";

let help: Document;
beforeAll(async () => {
  const container = await AstroContainer.create();
  help = parsePage(
    await container.renderToString(Help, { request: new Request("https://dmbot.example/help") }),
  );
});

const section = (): Element => {
  const found = help.querySelector("#getting-help");
  if (!found) throw new Error("no Getting help section");
  return found;
};
const words = (): string =>
  gettingHelp.items
    .map((i) => `${i.lead} ${i.parts.map((p) => (typeof p === "string" ? p : p.text)).join("")}`)
    .join(" ");

describe("Getting help", () => {
  it("is a section of the Help page, after the steps, with its own heading", () => {
    const sections = [...help.querySelectorAll("main section")].map((s) => s.getAttribute("id") ?? s.getAttribute("aria-labelledby"));
    expect(sections.at(-1)).toBe("getting-help");
    expect(help.querySelector("#getting-help-heading")?.textContent).toBe("Getting help");
    expect(section().getAttribute("aria-labelledby")).toBe("getting-help-heading");
  });

  it("lists the four ways in order: look here, ask in public, paid plans, anyone", () => {
    const leads = [...section().querySelectorAll("li strong")].map((s) => s.textContent);
    expect(leads).toEqual([
      "Look here first.",
      "Ask in public.",
      "Have an account?",
      "Anyone, on any plan.",
    ]);
  });

  it("the Q&A link goes to the Q&A for DMbot category", () => {
    const link = [...section().querySelectorAll("a")].find((a) => a.textContent === "Q&A for DMbot");
    expect(link?.getAttribute("href")).toBe(QA_URL);
    expect(QA_URL).toBe("https://github.com/smv620/Discord-DMbot/discussions/categories/q-a-for-dmbot");
  });

  it("says the public Q&A is public and says not to post account or payment details", () => {
    const item = section().querySelectorAll("li")[1]?.textContent ?? "";
    expect(item).toContain("Everything there is public");
    expect(item).toContain("don't post account or payment details");
    // QAbot "may answer first": true whether or not it is live yet (it is held back in What's new
    // until it is), and it says plainly that it is a bot.
    expect(item).toContain("QAbot (a bot, not a person) may answer first");
    expect(item).not.toContain("answers what it can");
  });

  it("points paid plans to the Support form on the account page, and says the reply is by email", () => {
    const item = section().querySelectorAll("li")[2];
    expect(item?.textContent).toContain("Write to the team in private");
    // Only "if it has one": an account with no email can't be answered by email (the form says so).
    expect(item?.textContent).toContain(
      "The team replies by email to the address on your account, if it has one",
    );
    expect(item?.querySelector("a")?.getAttribute("href")).toBe("/account");
  });

  it("the support address is support@getdmbot.com, as a mail link, and the only one named", () => {
    expect(SUPPORT_EMAIL).toBe("support@getdmbot.com");
    const mail = [...section().querySelectorAll("a")].filter((a) => a.getAttribute("href")?.startsWith("mailto:"));
    expect(mail.map((a) => [a.getAttribute("href"), a.textContent])).toEqual([
      ["mailto:support@getdmbot.com", "support@getdmbot.com"],
    ]);
  });

  // Two addresses, each for one job: support@ is where people get help (the Help page);
  // admin@ is the legal contact (content/legal.ts `operator.email`, #1402). Nothing else.
  it("no other getdmbot.com address is named on the Help page or anywhere in web/src", () => {
    const addresses = new Set<string>();
    const scan = (text: string): void => {
      // Subdomains too (a dev.getdmbot.com address would be a different address).
      for (const [a] of text.matchAll(/[\w.+-]+@(?:[\w-]+\.)*getdmbot\.com/gi)) addresses.add(a.toLowerCase());
    };
    scan(help.documentElement.outerHTML);
    const walk = (dir: string): void => {
      for (const name of readdirSync(dir)) {
        const full = join(dir, name);
        if (statSync(full).isDirectory()) walk(full);
        else if (/\.(ts|tsx|astro)$/.test(name)) scan(readFileSync(full, "utf8"));
      }
    };
    walk(fileURLToPath(new URL("../src/", import.meta.url)));
    // info@ is the sender of the "we got your question" email, named on the privacy page while
    // the support helper is on (#1432); support@ is where people get help (this page).
    expect([...addresses].sort()).toEqual(["admin@getdmbot.com", "info@getdmbot.com", "support@getdmbot.com"]);
  });

  it("uses none of the banned technical terms, no trademark, and makes no privacy promise of its own", () => {
    expect(words()).not.toMatch(/knowledge graph|ontology|claims|extractor|database|API\b|LLM|transcription|ticket/i);
    expect(words()).not.toMatch(/D&D|Dungeons|Wizards|Beyond|sourcebook/i);
    // Retention, deletion and who-can-read promises live on the privacy page and in the form.
    expect(words()).not.toMatch(/months|delete|deleted|encrypt|never stored|kept for|we keep/i);
    // Each way to get help names one thing to do.
    for (const item of gettingHelp.items) expect(item.lead.length, item.lead).toBeGreaterThan(5);
  });

  it("is linked from the footer and from the top of the Help page", () => {
    const footer = [...help.querySelectorAll("footer a")].map((a) => [a.getAttribute("href"), a.textContent]);
    expect(footer).toContainEqual(["/help#getting-help", "Get help"]);
    const top = help.querySelector('main a[href="#getting-help"]');
    expect(top).not.toBeNull();
    const firstHeading = help.querySelector("main h2");
    // DOCUMENT_POSITION_FOLLOWING (4): the first section heading comes after the link.
    expect(top && firstHeading && (top.compareDocumentPosition(firstHeading) & 4) !== 0).toBe(true);
  });

  it("has a held entry for the public Q&A, with the reason (What's new's own test checks it isn't shown)", () => {
    const entry = held.find((h) => h.title === "Ask a question in public");
    expect(entry?.reason).toContain("QAbot answers only once it is installed and live");
  });
});
