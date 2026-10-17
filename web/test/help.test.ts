// The Help page (#1142): every button and command it names is one the bot really shows, so
// the page can't drift from core/src/dmbot.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { beforeAll, describe, expect, it } from "vitest";

import { parsePage } from "./dom";

import { entries, held } from "../src/content/whats-new";
import { SHARED_STORY_LIVE, sharedStorySteps, STANDING_LIVE, standingSteps, HOMEBREW_KINDS_LIVE, homebrewKindSteps, STORY_THREADS_LIVE, storyThreadSteps, DM_BOT_CHANNEL_LIVE, dmBotChannelSteps, settingsOnDmScreenSteps, HOMEBREW_LINKS_LIVE, SECRET_CLUES_LIVE, accountSteps, dmSteps, homebrewLinkSteps, secretClueSteps, emailSteps, recapSteps, quickAnswers, quickAnswersHeading, gettingHelp, playerSteps } from "../src/content/help";
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
    for (const step of [...dmSteps, ...dmBotChannelSteps, ...settingsOnDmScreenSteps, ...storyThreadSteps, ...sharedStorySteps, ...standingSteps, ...homebrewKindSteps, ...homebrewLinkSteps, ...secretClueSteps, ...recapSteps, ...quickAnswers, ...playerSteps, ...accountSteps, ...emailSteps]) {
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

describe("Help: the website's read-only pages (#1588)", () => {
  it("has an entry for each, with a link to its page, in the players' section", async () => {
    const container = await AstroContainer.create();
    const page = parsePage(
      await container.renderToString(Help, { request: new Request("https://dmbot.example/help") }),
    );
    const players = [...page.querySelectorAll("#player-heading ~ ol li")];
    for (const [href, word] of [
      ["/account/house-rules", /house rules/i],
      ["/account/homebrew", /homebrew/i],
      ["/account/transcripts", /game/i],
    ] as const) {
      const item = players.find((li) => li.querySelector(`a[href="${href}"]`));
      expect(item, href).toBeTruthy();
      expect(item?.textContent).toMatch(word);
    }
  });

  it("uses plain words: no technical terms", () => {
    const texts = playerSteps.filter((s) => s.link).map((s) => s.text);
    expect(texts).toHaveLength(3);
    for (const t of texts) expect(t).not.toMatch(/\bAPI\b|\bdoor\b|database|\bJSON\b/i);
  });

  it("adds homebrew by typing or a file, and says what off means (#1653)", () => {
    const steps = dmSteps.map((x) => x.text);
    const adding = steps.find((t) => t.includes("Who made these?")) ?? "";
    expect(adding).toContain("➕ Add");
    expect(adding).toContain("Who made these?");
    expect(adding).toContain("House rules");
    const off = steps.find((t) => t.includes("Turn off")) ?? "";
    expect(off).toContain("An entry that is off is kept, and DMbot ignores it.");
    expect(off).toContain("Turn on");
  });

  it("holds the link-import steps with the held What's new entries, and flips them together (#1653)", () => {
    const heldTitles = held.map((h) => h.title);
    const linkTitles = [
      "Add homebrew from a link",
      "Share imported homebrew in a pack",
      "See the sites you approved",
    ];
    const liveTitles = entries.map((e) => e.title);
    for (const title of linkTitles) {
      expect(heldTitles.includes(title), title).toBe(!HOMEBREW_LINKS_LIVE);
      expect(liveTitles.includes(title), title).toBe(HOMEBREW_LINKS_LIVE);
    }
    const shown = dmSteps.map((x) => x.text).join(" ");
    expect(shown.includes("🔗 Add from a link")).toBe(HOMEBREW_LINKS_LIVE);
    const words = homebrewLinkSteps.map((x) => x.text).join(" ");
    expect(words).toContain("isn't public, publish it there first");
    expect(words).toContain("never asks again for that site");
    expect(words).toContain("Every table that uses that page gets the update");
    expect(words).toContain("Use example.com");
  });

  it("holds the DMbot channel steps with the held What's new entry, and flips them together (#1676)", () => {
    expect(held.map((h) => h.title).includes("A DMbot channel for buttons")).toBe(!DM_BOT_CHANNEL_LIVE);
    const shown = dmSteps.map((x) => x.text).join(" ");
    expect(shown.includes("dmb-dm-bot")).toBe(DM_BOT_CHANNEL_LIVE);
    expect(shown.includes("On your DM screen, press ⚙️ Settings")).toBe(!DM_BOT_CHANNEL_LIVE);
    // Mirrors who gets which button in core/src/dmbot/dm_screen/dm_bot_panel.py (_BUTTONS).
    const words = dmBotChannelSteps.map((x) => x.text).join(" ");
    expect(words).toContain("👤 My buttons");
    expect(words).toContain("Everyone also gets 📖 Look up a rule and 📜 Transcripts.");
    expect(words).toContain("Press 📖 Look up a rule to check a rule. Only you see the answer.");
    expect(words).toContain("you get ⚙️ Menu, where you can stop being recorded.");
    expect(words).toContain("Players also get 📜 My character sheet.");
    expect(words).toContain("Game masters get ⚙️ Settings too.");
    expect(words).toContain("/dmbot settings");
    expect(words).toContain("Your DM screen is quieter now");
    // Switch off: nothing shown points to the channel. Switch on: nothing shown says Settings is
    // on the DM screen or the pinned card.
    if (DM_BOT_CHANNEL_LIVE) {
      expect(shown).not.toMatch(/(?:on|from) (?:your|the) DM screen, press ⚙️ Settings|⚙️ Settings[^.]*on (?:your|the) DM screen|pinned card/);
    } else {
      for (const phrase of ["dmb-dm-bot", "DMbot channel", "My buttons"]) expect(shown).not.toContain(phrase);
    }
  });

  it("holds the story-thread steps with the held What's new entries, and flips them together (#1694)", () => {
    const heldTitles = held.map((h) => h.title);
    for (const title of ["Story threads you keep", "Keep track of promises made at the table"]) {
      expect(heldTitles.includes(title), title).toBe(!STORY_THREADS_LIVE);
    }
    const shown = dmSteps.map((x) => x.text).join(" ");
    expect(shown.includes("🧵 New thread")).toBe(STORY_THREADS_LIVE);
    const words = storyThreadSteps.map((x) => x.text).join(" ");
    for (const label of ["🧵 Change state", "👥 Who's involved", "📌 Link an event", "🧵 Check the threads", "🧵 Threads", "🤝 Add a promise", "🤝 Who promised", "🤝 Promised to", "📅 Due", "➡ Led to…"]) {
      expect(words, label).toContain(label);
    }
    expect(words).toContain("DMbot only reminds you. You decide.");
    expect(words).toContain("DMbot never sends your threads, promises or these links to players.");
  });

  it("holds the standing and homebrew-kind steps with their held What's new entries (#1695)", () => {
    const heldTitles = held.map((h) => h.title);
    expect(heldTitles.includes("How NPCs feel about the party")).toBe(!STANDING_LIVE);
    for (const title of ["Homebrew menu by kind", "Species, subclasses and backgrounds in homebrew"]) {
      expect(heldTitles.includes(title), title).toBe(!HOMEBREW_KINDS_LIVE);
    }
    const shown = dmSteps.map((x) => x.text).join(" ");
    expect(shown.includes("🤝 Standing")).toBe(STANDING_LIVE);
    expect(shown.includes("↩ All kinds")).toBe(HOMEBREW_KINDS_LIVE);
    expect(standingSteps.map((x) => x.text).join(" ")).toContain("Players never see it.");
    expect(homebrewKindSteps.map((x) => x.text).join(" ")).toContain("Spells 10/12, which means 10 of 12 spells are on.");
    expect(secretClueSteps.map((x) => x.text).join(" ")).toContain("If a player asks DMbot about it, DMbot won't show a clue that only some people know.");
  });

  it("holds the Shared story steps with the held What's new entry, and flips them together (#1720)", () => {
    expect(held.map((h) => h.title).includes("A Shared story switch")).toBe(!SHARED_STORY_LIVE);
    const shown = dmSteps.map((x) => x.text).join(" ");
    expect(shown.includes("🧭 Shared story")).toBe(SHARED_STORY_LIVE);
    const words = sharedStorySteps.map((x) => x.text).join(" ");
    expect(words).toContain("Not working yet: for now DMbot can keep and read your adventure, but nothing uses what it read, so you get no alerts.");
    // Read it now (#1725) goes live with the same held entry, since that entry already describes the reading.
    expect(words).toContain("press 📖 Read it now on its card");
    expect(words).toContain("Players never see the list. It doesn't change your alerts yet.");
    expect(words).toContain("The list isn't in the campaign's backup");
    expect(words).not.toMatch(/\$|token|\d+ (pages|files)/i);
    expect(words).toContain("with Off, DMbot remembers your table's story only.");
    expect(words).toContain("With Watch the story, it will also tell you when your table and the book no longer fit.");
    expect(words).toContain("With Guide me, when you ask, it will say what the book has next, with the page.");
    expect(words).toContain("Only give DMbot material you have the right to use. DMbot doesn't check this.");
    expect(words).toContain("DMbot keeps the text, not the file, for this campaign only. Players can't open it.");
    expect(words).toContain('"Not now" to leave it off');
    expect(words).toContain("keeps only your adventure's name, not its text");
  });

  it("finds Settings, splits the party and deletes a campaign in plain words (#1659)", () => {
    const steps = dmSteps.map((x) => x.text);
    const find = steps.find((t) => t.includes("Can't find ⚙️ Settings?")) ?? "";
    expect(find).toContain("/dmbot settings");
    // The voice-channel clause belongs to the DM-screen wording; the DMbot channel replaces it (#1676).
    if (!DM_BOT_CHANNEL_LIVE) expect(find).toContain("/dmbot start");
    const split = steps.find((t) => t.includes("✂️ Split the party")) ?? "";
    expect(split).toContain("two or three groups");
    expect(split).toContain("🤝 Rejoin the party");
    const del = steps.find((t) => t.includes("🗑 Delete this campaign")) ?? "";
    expect(del).toContain("whose plan a campaign uses");
    expect(del).toContain("It can't be undone.");
    expect(del).toContain("type the campaign's name");
    expect(del).toContain("💾 Back up first");
  });

  it("holds the secrets step with the held What's new entry, and flips them together (#1659)", () => {
    const heldTitle = held.map((h) => h.title).includes("Secrets only some people know");
    expect(heldTitle).toBe(!SECRET_CLUES_LIVE);
    const shown = dmSteps.map((x) => x.text).join(" ");
    expect(shown.includes("Only some know this")).toBe(SECRET_CLUES_LIVE);
    const words = secretClueSteps.map((x) => x.text).join(" ");
    expect(words).toContain("DMbot never decides who learned it. You do.");
  });
});
