// The Help page (#1142): every button and command it names is one the bot really shows, so
// the page can't drift from core/src/dmbot.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { beforeAll, describe, expect, it } from "vitest";

import { parsePage } from "./dom";

import { entries, held } from "../src/content/whats-new";
import { storyStatesLine } from "../src/content/privacy-channels";
import { featureSteps, isLive, knownTitle, liveFeatureSteps } from "../src/content/help-features";
import { WHATS_ON_LIVE, whatsOnSteps, TIME_SUGGESTIONS_LIVE, timeSuggestionSteps, RULES_CHANNEL_LIVE, rulesChannelSteps, INITIATIVE_STYLE_LIVE, initiativeStyleSteps, initiativeStyleDmSteps, NPCS_CHANNEL_LIVE, npcsChannelSteps, DEEDS_LIVE, deedSteps, WHERE_ARE_WE_LIVE, whereAreWeSteps, SHARED_STORY_LIVE, sharedStorySteps, STANDING_LIVE, standingSteps, HOMEBREW_KINDS_LIVE, homebrewKindSteps, STORY_THREADS_LIVE, storyThreadSteps, DM_BOT_CHANNEL_LIVE, dmBotChannelSteps, NAMES_BUTTON_LIVE, SIDEBAR_SOURCES_LIVE, namesButtonSteps, QUIET_STOP_LIVE, quietStopSteps, sidebarSourceSteps, settingsOnDmScreenSteps, HOMEBREW_LINKS_LIVE, SECRET_CLUES_LIVE, PRESESSION_NOTE_LIVE, SECRET_SAID_LIVE, SECRET_REVEAL_LIVE, presessionNoteSteps, secretSaidSteps, secretRevealSteps, RULE_KINDS_LIVE, ruleKindSteps, ruleKindDmSteps, FIGHT_AND_ITEM_RULES_LIVE, fightRuleSteps, magicItemRuleSteps, accountSteps, dmSteps, homebrewLinkSteps, secretClueSteps, emailSteps, recapSteps, quickAnswers, quickAnswersHeading, gettingHelp, playerSteps, snags, snagsHeading, onThisPage, onThisPageHeading, backToTop, STORY_STATES_LIVE, storyStateSteps } from "../src/content/help";
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
    expect(lists.map((l) => l.children.length)).toEqual([dmSteps.length, playerSteps.length, accountSteps.length, emailSteps.length, snags.length, gettingHelp.items.length]);
  });

  it("says what DMbot knows when the game master asks it a quick question (#1379)", () => {
    expect(help.querySelector("#quick-heading")?.textContent).toBe(quickAnswersHeading);
    const items = [...help.querySelectorAll("#quick-heading + ul li")].map((li) => li.textContent);
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
    for (const step of [...dmSteps, ...dmBotChannelSteps, ...namesButtonSteps, ...quietStopSteps, ...settingsOnDmScreenSteps, ...storyThreadSteps, ...sharedStorySteps, ...standingSteps, ...homebrewKindSteps, ...homebrewLinkSteps, ...secretClueSteps, ...presessionNoteSteps, ...secretSaidSteps, ...secretRevealSteps, ...recapSteps, ...quickAnswers, ...playerSteps, ...snags, ...accountSteps, ...emailSteps, ...featureSteps]) {
      for (const { name, file } of step.names ?? []) {
        expect(step.text, `${name} is in its step`).toContain(name);
        const source = readFileSync(join(botDir, file), "utf8");
        expect(source, `${name} in ${file}`).toContain(name);
      }
    }
  });

  it("has an If something doesn't work section of five to seven short answers (#1805)", () => {
    expect(snags.length).toBeGreaterThanOrEqual(5);
    expect(snags.length).toBeLessThanOrEqual(7);
    for (const snag of snags) expect(snag.text.split(" ").length, snag.question).toBeLessThanOrEqual(40);
    const section = help.querySelector("#snags-heading")?.parentElement;
    expect(section?.querySelector("h2")?.textContent).toBe(snagsHeading);
    expect(section?.querySelectorAll("li").length).toBe(snags.length);
    // The section sits before "Getting help" and links to the steps and the support route on this page.
    const hrefs = [...(section?.querySelectorAll("li a") ?? [])].map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual(["#dm-heading", "#getting-help"]);
    const order = [...help.querySelectorAll("h2")].map((h) => h.id);
    expect(order.indexOf("snags-heading")).toBe(order.indexOf("getting-help-heading") - 1);
  });

  it("has an On this page list whose links all point to ids on the page (#1811)", () => {
    const nav = help.querySelector("#contents-heading")?.closest("nav");
    expect(nav?.getAttribute("aria-labelledby")).toBe("contents-heading");
    expect(help.querySelector("#contents-heading")?.textContent).toBe(onThisPageHeading);
    const links = [...(nav?.querySelectorAll("a") ?? [])];
    expect(links.length).toBeGreaterThanOrEqual(6);
    const headingIds: string[] = [];
    for (const a of links) {
      const id = a.getAttribute("href")?.slice(1) ?? "";
      const target = help.getElementById(id);
      expect(target, `#${id} exists`).not.toBeNull();
      // A link may point at a section or at its heading; either way it says the heading's words.
      const heading = target?.tagName === "SECTION" ? target.querySelector("h2") : target;
      expect(heading?.textContent?.trim(), id).toBe(a.textContent?.trim());
      headingIds.push(heading?.id ?? "");
    }
    // In page order, and every h2 (besides this list's own) is listed.
    const order = [...help.querySelectorAll("h2")].map((h) => h.id).filter((id) => id !== "contents-heading");
    expect(order.every((id) => headingIds.includes(id))).toBe(true);
    expect(headingIds.filter((id) => order.includes(id))).toEqual(order);
  });

  it("lists the recaps heading only while recaps are on (#1811)", () => {
    expect(onThisPage(false, "Recaps").map((c) => c.id)).not.toContain("recaps-heading");
    expect(onThisPage(true, "Recaps").map((c) => c.id)).toContain("recaps-heading");
  });

  it("has Back to top links that point at the top of the page (#1811)", () => {
    const backs = [...help.querySelectorAll("a.back, .back a")];
    expect(backs.length).toBeGreaterThan(0);
    for (const a of backs) {
      expect(a.textContent?.trim()).toBe(backToTop);
      expect(help.getElementById(a.getAttribute("href")?.slice(1) ?? "")?.tagName).toBe("H1");
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
    expect(links).toContain("/players"); // the first step sends the game master's players there (#1821)
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
    expect(words).toContain("👤 DMbot Main Menu");
    expect(words).toContain("Everyone also gets 📖 Look up a rule and 📜 Transcripts.");
    expect(words).toContain("Press 📖 Look up a rule to check a rule. Only you see the answer.");
    expect(words).toContain("you get 🗂️ Menu, where you can stop being recorded.");
    expect(words).toContain("Players also get 📜 My character sheet.");
    expect(words).toContain("Game masters get ⚙️ Settings too.");
    expect(words).toContain("/dmbot settings");
    expect(words).toContain("Your DM screen is quieter now");
    // Switch off: nothing shown points to the channel. Switch on: nothing shown says Settings is
    // on the DM screen or the pinned card.
    if (DM_BOT_CHANNEL_LIVE) {
      expect(shown).not.toMatch(/(?:on|from) (?:your|the) DM screen, press ⚙️ Settings|⚙️ Settings[^.]*on (?:your|the) DM screen|pinned card/);
    } else {
      for (const phrase of ["dmb-dm-bot", "DMbot channel", "DMbot Main Menu"]) expect(shown).not.toContain(phrase);
    }
  });

  it("holds the 📇 Names button step with its held What's new entry, and flips them together (#2002)", () => {
    expect(held.map((h) => h.title).includes("A 📇 Names button")).toBe(!NAMES_BUTTON_LIVE);
    const shown = dmSteps.map((x) => x.text).join(" ");
    expect(shown.includes("📇 Names")).toBe(NAMES_BUTTON_LIVE);
    const words = namesButtonSteps.map((x) => x.text).join(" ");
    expect(words).toContain("search the names and fix one");
    expect(words).toContain("Any after-session check you haven't done yet (Check names, Check who is who, Check what was said) waits there.");
    expect(words).toContain("/dmbot names");
    expect(words).not.toMatch(/entit|knowledge graph/i);
    // The place named matches the channel's own switch: never "the DMbot channel" before it is live.
    expect(words.includes("DMbot channel")).toBe(DM_BOT_CHANNEL_LIVE);
    for (const step of namesButtonSteps) expect(step.text.split(/[.?!] /).length).toBeLessThanOrEqual(5);
  });

  it("holds the automatic stop step with its held What's new entry, and flips them together (#2041)", () => {
    expect(held.map((h) => h.title).includes("Stops listening when no one's there")).toBe(!QUIET_STOP_LIVE);
    const shown = dmSteps.map((x) => x.text).join(" ");
    expect(shown.includes("Keep listening")).toBe(QUIET_STOP_LIVE);
    const words = quietStopSteps.map((x) => x.text).join(" ");
    // The numbers mirror the bot (idle_stop.py, QUIET_STOP_CHOICES); the choices are the DM's alone.
    for (const phrase of ["5 minutes", "10 minutes", "15, 30 and 5", "there is no Off", "bot never counts as a person", "private message"]) {
      expect(words).toContain(phrase);
    }
    expect(words).not.toMatch(/idle|timeout|session id|heartbeat/i);
    for (const step of quietStopSteps) expect(step.text.split(/[.?!] /).length).toBeLessThanOrEqual(5);
  });

  it("holds the fight and magic item steps with their held What's new entry, and flips them together (#2023)", () => {
    expect(held.map((h) => h.title).includes("Look up fight difficulty rules")).toBe(!FIGHT_AND_ITEM_RULES_LIVE);
    const gm = dmSteps.map((x) => x.text).join(" ");
    const players = playerSteps.map((x) => x.text).join(" ");
    expect(gm.includes("XP budget")).toBe(FIGHT_AND_ITEM_RULES_LIVE);
    expect(players.includes("attunement")).toBe(FIGHT_AND_ITEM_RULES_LIVE);
    const fight = fightRuleSteps.map((x) => x.text).join(" ");
    // DMbot reads the rule and does no sum and no ruling (sidebar/context.py ENCOUNTER_NOTE): the words promise neither.
    expect(fight).toContain("It doesn't add up the monsters or say if the fight is easy or deadly: that's your call.");
    expect(fight).toContain("That's one for your DM.");
    expect(fight).not.toMatch(/calculat|encounter|budget math/i);
    // The fight step is the game master's alone; the magic item step is for everyone.
    expect(players).not.toContain("XP budget");
    for (const step of [...fightRuleSteps, ...magicItemRuleSteps]) expect(step.text.split(/[.?!] /).length).toBeLessThanOrEqual(5);
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
    expect(words).toContain("Guide me adds a button that says what the book has next.");
    // Read it now (#1725) goes live with the same held entry, since that entry already describes the reading.
    expect(words).toContain("press 📖 Read it now on its card, then Yes, read it");
    expect(words).toContain("When it's done, DMbot posts a line on the DM screen.");
    expect(words).toContain("which players never see");
    expect(words).toContain("not its text or what DMbot read");
    expect(words).not.toMatch(/\$|token|\d+ (pages|files)/i);
    expect(words).toContain("Off means DMbot remembers your table's story only.");
    expect(words).toContain("Watch the story also tells you when your table and the book no longer fit.");
    
    expect(words).toContain("Only give DMbot material you have the right to use. DMbot doesn't check this.");
    expect(words).toContain("DMbot keeps the text, not the file, for this campaign only. Players can't open it.");
    expect(words).toContain('"Not now" to leave it off');
    expect(words).toContain("keeps only your adventure's name, not its text");
    // In my adventure… (#1726) is held with its own What's new line and goes live with the same switch.
    expect(held.map((h) => h.title).includes("Link names to your adventure")).toBe(!SHARED_STORY_LIVE);
    expect(words).toContain("press 📖 In my adventure…, then pick them from the list");
    expect(words).toContain("DMbot never links names by itself");
    // Watch the story (#1735) works in the code and is held with its own What's new line.
    expect(held.map((h) => h.title).includes("Watch the story")).toBe(!SHARED_STORY_LIVE);
    expect(words).toContain("With Watch the story on, DMbot tells you, privately on the DM screen");
    expect(words).toContain("It never tells you what to do; that is your call.");
    // Guide me (#1736) works in the code and is held with its own What's new line.
    expect(held.map((h) => h.title).includes("Guide me: what's next in your adventure")).toBe(!SHARED_STORY_LIVE);
    expect(words).toContain("With Guide me on, press 🧭 What's next?");
    expect(words).toContain("It never decides what happens.");
    expect(words).toContain("then Take off. Made a mistake? Press Undo that.");
  });

  it("says a backup keeps how often names were said, only for people still recorded (#1754)", () => {
    const step = dmSteps.map((x) => x.text).find((t) => t.includes("/dmbot backup (paid campaigns only)")) ?? "";
    expect(step).toContain("how often names were said and by whom");
    expect(step).toContain("only for people who are still recorded");
  });

  it("holds the rules channel steps with their held What's new entry (#1759)", () => {
    expect(held.map((h) => h.title).includes("A channel for your house rules")).toBe(!RULES_CHANNEL_LIVE);
    const shown = dmSteps.map((x) => x.text).join(" ");
    expect(shown.includes("a channel called dmb-rules")).toBe(RULES_CHANNEL_LIVE);
    const words = rulesChannelSteps.map((x) => x.text).join(" ");
    expect(words).toContain("Only DMbot posts in dmb-rules. Players can't, and neither can you.");
    expect(words).toContain(NPCS_CHANNEL_LIVE ? "closes all three." : "Hiding the DM screen again closes both.");
    expect(words).toContain("Each rule is shown in your own words.");
  });

  it("says to pick /dmbot start from the list, with a tip for when no list pops up (#1770)", () => {
    const words = dmSteps.map((x) => x.text).join(" ");
    expect(words).toContain("Wait for the list to pop up, pick /dmbot start in it, then send it.");
    expect(words).toContain("No list pops up?");
    expect(words).toContain("If it went out as plain chat, DMbot didn't get it.");
  });

  it("holds the NPC list channel steps with their held What's new entry (#1781)", () => {
    expect(held.map((h) => h.title).includes("A channel listing the people and groups")).toBe(!NPCS_CHANNEL_LIVE);
    const shown = dmSteps.map((x) => x.text).join(" ");
    expect(shown.includes("a channel called dmb-npcs")).toBe(NPCS_CHANNEL_LIVE);
    const words = npcsChannelSteps.map((x) => x.text).join(" ");
    expect(words).toContain("Only DMbot posts there.");
    expect(words).toContain("the next /dmbot start fixes it");
    // The peek sentence names the NPC list only when the channel is live.
    const peek = rulesChannelSteps.map((x) => x.text).join(" ");
    expect(peek.includes("dmb-rules and dmb-npcs too")).toBe(NPCS_CHANNEL_LIVE);
    for (const step of npcsChannelSteps) expect(step.text.split(" ").length).toBeLessThanOrEqual(40);
  });

  it("holds the initiative style steps with their held What's new entry (#1788)", () => {
    expect(held.map((h) => h.title).includes("FightBot follows your turn order")).toBe(!INITIATIVE_STYLE_LIVE);
    const players = playerSteps.map((x) => x.text).join(" ");
    expect(players.includes("FightBot listens for how your table runs initiative, and times")).toBe(INITIATIVE_STYLE_LIVE);
    const dm = dmSteps.map((x) => x.text).join(" ");
    expect(dm.includes("Initiative, as heard:")).toBe(INITIATIVE_STYLE_LIVE);
    // Players don't see the combat line, so only the game master's steps speak of it.
    expect(initiativeStyleSteps.map((x) => x.text).join(" ")).not.toContain("combat line");
    const words = initiativeStyleDmSteps.map((x) => x.text).join(" ");
    expect(words).toContain("There is no button.");
    expect(words).toContain("as heard last time");
    expect(words).not.toMatch(/setting/i); // the side-initiative button and setting are gone (#1774)
    for (const step of [...initiativeStyleSteps, ...initiativeStyleDmSteps]) {
      expect(step.text.split(" ").length).toBeLessThanOrEqual(40);
    }
  });

  it("holds the sidebar sources step with its held What's new entry (#1958)", () => {
    expect(held.map((h) => h.title).includes("See what DMbot looked at")).toBe(!SIDEBAR_SOURCES_LIVE);
    const shown = quickAnswers.map((x) => x.text).join(" ");
    expect(shown.includes("Where did that come from?")).toBe(SIDEBAR_SOURCES_LIVE);
    for (const step of sidebarSourceSteps) {
      expect(step.text.split(/[.?!] /).length).toBeLessThanOrEqual(5);
      expect(step.text).toContain("📖 What I looked at");
    }
    expect(held.find((h) => h.title === "See what DMbot looked at")?.reason).toContain("#1957");
  });

  it("holds the time suggestion step with its held What's new entry (#1765)", () => {
    expect(held.map((h) => h.title).includes("A time suggestion from your narration")).toBe(!TIME_SUGGESTIONS_LIVE);
    const shown = dmSteps.map((x) => x.text).join(" ");
    expect(shown.includes("DMbot may suggest a jump on your DM screen")).toBe(TIME_SUGGESTIONS_LIVE);
    const words = timeSuggestionSteps.map((x) => x.text).join(" ");
    expect(words).toContain("press Not now to skip it, or Undo to put it back. Players never see it.");
    expect(words).toContain("only if you agreed to recording");
    expect(words).toContain("It is off unless the person who runs your DMbot turns it on.");
  });

  it("holds the What's switched on step and snag with their held What's new entry (#1841)", () => {
    expect(held.map((h) => h.title).includes("See what's switched on")).toBe(!WHATS_ON_LIVE);
    const shown = dmSteps.map((x) => x.text).join(" ");
    expect(shown.includes("Wondering if something new is on yet?")).toBe(WHATS_ON_LIVE);
    expect(snags.some((x) => x.text.includes("What's switched on"))).toBe(WHATS_ON_LIVE);
    const words = whatsOnSteps.map((x) => x.text).join(" ");
    expect(words).toContain("Only you see the answer");
    for (const step of whatsOnSteps) expect(step.text.split(" ").length).toBeLessThanOrEqual(40);
  });

  it("holds the deeds and where-are-we steps with their held What's new entries (#1754)", () => {
    const heldTitles = held.map((h) => h.title);
    expect(heldTitles.includes("Record what your players did")).toBe(!DEEDS_LIVE);
    expect(heldTitles.includes("A quiet reminder of the game time")).toBe(!WHERE_ARE_WE_LIVE);
    const shown = dmSteps.map((x) => x.text).join(" ");
    expect(shown.includes("⚔️ Record a deed")).toBe(DEEDS_LIVE);
    expect(shown.includes("nobody has shown or moved it for half an hour")).toBe(WHERE_ARE_WE_LIVE);
    const deeds = deedSteps.map((x) => x.text).join(" ");
    expect(deeds).toContain("Players never see deeds, and they are never in a transcript.");
    expect(deeds).toContain("counts for less after ten sessions");
    expect(deeds).toContain("It never listens for deeds.");
    const clock = whereAreWeSteps.map((x) => x.text).join(" ");
    expect(clock).toContain("Players never see it.");
    expect(clock).toContain("Quiet ones never");
    // The note's exact line is not quoted: its wording is still being settled (#1745).
    expect(clock).not.toContain("Where we are");
    const guide = sharedStorySteps.map((x) => x.text).join(" ");
    expect(guide).toContain("Hey DMbot, what's next?");
    expect(guide).toContain("the transcript only notes that DMbot answered privately");
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

  it("holds the alive-and-carries words with the held What's new entry and the privacy line (#1284)", () => {
    const heldTitle = held.map((h) => h.title).includes("Who is alive, who carries what");
    expect(heldTitle).toBe(!STORY_STATES_LIVE);
    const shown = dmSteps.map((x) => x.text).join(" ");
    expect(shown.includes("remembers who is alive or dead")).toBe(STORY_STATES_LIVE);
    const words = storyStateSteps.map((x) => x.text).join(" ");
    expect(storyStatesLine.live).toBe(STORY_STATES_LIVE);
    expect(words).toContain("You decide");
    expect(words).toContain("Players never do, and it is never in a transcript");
    expect(words).not.toMatch(/state fact|ontology|knowledge graph|claim/i);
  });

  it("holds the rule-kinds step with the held \"Look up any rule\" entry, and flips them together (#1924)", () => {
    const heldTitle = held.map((h) => h.title).includes("Look up any rule");
    expect(heldTitle).toBe(!RULE_KINDS_LIVE);
    const shown = playerSteps.map((x) => x.text).join(" ");
    expect(shown.includes("also finds rules like cover or long rest")).toBe(RULE_KINDS_LIVE);
    const words = ruleKindSteps.map((x) => x.text).join(" ");
    for (const example of ["cover", "rage", "Bag of Holding", "Alert", "Goliath", "rogue evasion", "Orc (creature)"]) {
      expect(words, example).toContain(example);
    }
    expect(words).toContain("longsword"); // weapons and armor merged with #1921
    // The same kinds as the entry: the Help step names what the entry names.
    const entry = held.find((h) => h.title === "Look up any rule")?.what ?? "";
    expect(entry.match(/longsword/gi)?.length, "longsword once in the entry").toBe(1);
    expect(words.match(/longsword/gi)?.length, "longsword once in the Help step").toBe(1);
    for (const example of ["cover", "rage", "Bag of Holding", "longsword", "Alert", "Goliath"]) {
      expect(entry, example).toContain(example);
    }
  });

  it("explains the 2014 mark and the game master's own entries, only while the rule kinds are live (#1961)", () => {
    const player = playerSteps.map((x) => x.text).join(" ");
    const dm = dmSteps.map((x) => x.text).join(" ");
    expect(player.includes("marked [Legacy 2014].")).toBe(RULE_KINDS_LIVE);
    expect(player.includes("That's one for your DM.")).toBe(RULE_KINDS_LIVE);
    expect(dm.includes("things for you alone, like traps, poisons, curses and diseases")).toBe(RULE_KINDS_LIVE);
    const words = [...ruleKindSteps, ...ruleKindDmSteps].map((x) => x.text).join(" ");
    expect(words).not.toMatch(/fallback|index|SRD 5\.1/i); // the source line on each answer names the book
    // The entry says the same three things, so the held entry and the steps flip together.
    const entry = held.find((h) => h.title === "Look up any rule")?.what ?? "";
    for (const part of ["[Legacy 2014]", "like traps, poisons, curses and diseases", "That's one for your DM"]) {
      expect(entry, part).toContain(part);
    }
  });

  it("holds the secrets step with the held What's new entry, and flips them together (#1659)", () => {
    const heldTitle = held.map((h) => h.title).includes("Secrets only some people know");
    expect(heldTitle).toBe(!SECRET_CLUES_LIVE);
    const shown = dmSteps.map((x) => x.text).join(" ");
    expect(shown.includes("Only some know this")).toBe(SECRET_CLUES_LIVE);
    const words = secretClueSteps.map((x) => x.text).join(" ");
    expect(words).toContain("DMbot never decides who learned it. You do.");
  });

  it("holds the note-before-a-session step with its held What's new entry, and flips them together (#2069)", () => {
    const heldTitle = held.map((h) => h.title).includes("A note before tonight's session");
    expect(heldTitle).toBe(!PRESESSION_NOTE_LIVE);
    const shown = dmSteps.map((x) => x.text).join(" ");
    expect(shown.includes("read it before you begin")).toBe(PRESESSION_NOTE_LIVE);
    // Hidden while the switch is false, but the step itself is still checked.
    const words = presessionNoteSteps.map((x) => x.text).join(" ");
    for (const part of ["last time's recap", "wait for your check", "DMbot never sends it to players", "If there is nothing to say, there is no note"]) expect(words).toContain(part);
    // Only what the built note holds: no button, no story-thread or guide line behind another switch.
    expect(words).not.toMatch(/\bpress\b|thread|promise|What's next/i);
    const entry = held.find((h) => h.title === "A note before tonight's session")?.what ?? "";
    for (const part of ["last time's recap", "anything lost or ended", "wait for your check"]) {
      expect(entry, part).toContain(part);
    }
  });

  it("holds the secret-name question step with its held What's new entry, and flips them together (#2069)", () => {
    const heldTitle = held.map((h) => h.title).includes("When a player says a secret name");
    expect(heldTitle).toBe(!SECRET_SAID_LIVE);
    const shown = dmSteps.map((x) => x.text).join(" ");
    expect(shown.includes("Press Not secret any more")).toBe(SECRET_SAID_LIVE);
    const words = secretSaidSteps.map((x) => x.text).join(" ");
    for (const part of ["five at most", "make it secret again from the name's card", "covers every copy", "Only the campaign's DMs can answer", "DMbot never decides for you"]) expect(words).toContain(part);
    // The buttons are named as the code labels them, with the file the label check reads.
    expect(secretSaidSteps.flatMap((x) => x.names ?? []).map((n) => n.name)).toEqual(["Keep it secret", "Not secret any more"]);
    const entry = held.find((h) => h.title === "When a player says a secret name")?.what ?? "";
    for (const part of ["one name at a time (up to five)", "never decides for you"]) {
      expect(entry, part).toContain(part);
    }
  });

  it("holds the question about a secret name the game master says with its held What's new entry (#2102)", () => {
    const heldTitle = held.map((h) => h.title).includes("When you say a secret name");
    expect(heldTitle).toBe(!SECRET_REVEAL_LIVE);
    const shown = dmSteps.map((x) => x.text).join(" ");
    expect(shown.includes("Press Yes, players know now")).toBe(SECRET_REVEAL_LIVE);
    const words = secretRevealSteps.map((x) => x.text).join(" ");
    for (const part of ["make it secret again from the name's card", "sent to you as a private message instead", "this session", "never when a player does", "covers every copy", "never decides for you", "Only the campaign's DMs can answer"]) expect(words).toContain(part);
    expect(secretRevealSteps.flatMap((x) => x.names ?? []).map((n) => n.name)).toEqual(["Yes, players know now", "No, keep it secret"]);
  });
});

describe("Help for the features of 2026-10-22/23 (#2498)", () => {
  it("is tied to real What's new entries, so a rename can't hide a step for ever", () => {
    for (const s of featureSteps) expect(knownTitle(s.whatsNew), s.whatsNew).toBe(true);
  });

  it("shows a step only once its What's new entry is out of `held`", () => {
    for (const audience of ["gameMaster", "player", "account"] as const) {
      const shown = liveFeatureSteps(audience).map((x) => x.text);
      for (const s of featureSteps.filter((f) => f.audience === audience))
        expect(shown.includes(s.text), s.whatsNew).toBe(isLive(s.whatsNew));
    }
  });

  it("says plainly that nothing is recorded while paused and that it isn't added afterwards", () => {
    const off = featureSteps.filter((s) => s.whatsNew === "Go off the record for a while").map((s) => s.text).join(" ");
    expect(off).toContain("records nothing");
    expect(off).toContain("isn't added afterwards");
    expect(off).toContain("Nothing said during the break is saved");
  });

  it("uses no technical words", () => {
    for (const s of featureSteps) expect(s.text, s.whatsNew).not.toMatch(/entit|knowledge graph|API|queue|endpoint/i);
  });
});
