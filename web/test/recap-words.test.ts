// The words about recaps on Help, the emails list and the privacy page (#1384). They must show
// only while recaps are live, and every sentence must say what the code does (the pull request
// lists the lines).
import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { beforeAll, describe, expect, it } from "vitest";

import { parsePage } from "./dom";

import RecapsPrivacy from "../src/components/RecapsPrivacy.astro";
import { aiTrainingSentence } from "../src/content/legal";
import { emailSteps } from "../src/content/help";
import {
  RECAP_AUDIO_LIVE,
  RECAP_NAMES_LIVE,
  RECAP_SHARE_LIVE,
  RECAPS_LIVE,
  emailsIntroTail,
  recapEmailStep,
  recapSteps,
  recapNamesStep,
} from "../src/content/recap-words";
import { entries, held } from "../src/content/whats-new";
import Help from "../src/pages/help.astro";
import Privacy from "../src/pages/legal/privacy.astro";

const text = (node: Element | Document | null): string =>
  (node?.textContent ?? "").replace(/\s+/g, " ").trim();

let off: Document;
let on: Document;
let onAudio: Document;
let shared: Document;
let sharedAudio: Document;
let help: Document;
let privacy: Document;
beforeAll(async () => {
  const container = await AstroContainer.create();
  const render = async (live: boolean, audio: boolean): Promise<Document> =>
    parsePage(await container.renderToString(RecapsPrivacy, { props: { live, audio } }));
  off = await render(false, false);
  on = await render(true, false);
  onAudio = await render(true, true);
  const renderShared = async (audio: boolean): Promise<Document> =>
    parsePage(await container.renderToString(RecapsPrivacy, { props: { live: true, audio, share: true } }));
  shared = await renderShared(false);
  sharedAudio = await renderShared(true);
  help = parsePage(
    await container.renderToString(Help, { request: new Request("https://dmbot.example/help") }),
  );
  privacy = parsePage(
    await container.renderToString(Privacy, {
      request: new Request("https://dmbot.example/legal/privacy"),
    }),
  );
});

describe("the privacy section", () => {
  it("is on the page exactly while recaps are live, and says nothing when switched off", () => {
    expect(RECAP_AUDIO_LIVE).toBe(false); // narration waits for the owner's ElevenLabs go-ahead
    expect(text(off.body)).toBe("");
    expect(privacy.querySelector("#recaps") !== null).toBe(RECAPS_LIVE);
  });

  it("says who writes it, who sees it, and how long it is kept", () => {
    const t = text(on.body);
    expect(t).toContain("The AI company named above (Anthropic)");
    expect(t).toContain(aiTrainingSentence);
    expect(t).toContain("never the ones marked secret");
    expect(t).toContain("Only the game masters of that campaign, until one of them presses Post");
    expect(t).toContain("the email has the title (or the reason) and a link, never the recap");
    expect(t).toContain("DMbot won't post a recap if a player has stopped being recorded");
    expect(t).toContain("are kept with the campaign and deleted when the campaign is deleted");
    expect(t).toContain("with no words in it, and delete it after 90 days");
  });

  it("says nothing of a shared link until sharing is live, and then says it plainly (#2119)", () => {
    expect(RECAP_SHARE_LIVE).toBe(false);
    expect(!RECAP_SHARE_LIVE || RECAPS_LIVE).toBe(true);
    expect(text(on.body)).not.toMatch(/shared link|has it can read/i);
    const t = text(shared.body);
    for (const part of [
      "even people outside your Discord server, and they can pass it on",
      "only a request: treat the link like a password",
      "turn a link off or make a new one",
      "no longer opens the recap, though anyone who already saw it may keep a copy",
      "A posted recap is already in the table's chat channel for everyone there",
    ]) {
      expect(t, part).toContain(part);
    }
    expect(t).not.toContain("play its voice and music");
    expect(text(sharedAudio.body)).toContain("play its voice and music without signing in");
  });

  it("names ElevenLabs only when audio is live", () => {
    expect(text(on.body)).not.toContain("ElevenLabs");
    expect(text(onAudio.body)).toContain("go to ElevenLabs");
    expect(text(onAudio.body)).toContain("We do not keep the audio files on our server");
  });

  it("uses no technical terms", () => {
    expect(text(onAudio.body)).not.toMatch(
      /knowledge graph|ontology|extractor|database|API\b|LLM|token|beat sheet|grader/i,
    );
  });
});

describe("Help and the emails list", () => {
  it("show nothing about recaps exactly while recaps are off", () => {
    expect(help.querySelector("#recaps-heading") !== null).toBe(RECAPS_LIVE);
    expect(/recap/i.test(emailSteps.map((s) => s.text).join(" "))).toBe(RECAPS_LIVE);
    expect(emailsIntroTail.includes("when a recap is ready")).toBe(RECAPS_LIVE);
  });

  it("pin the sentences for when they are on", () => {
    const all = recapSteps.map((s) => s.text).join(" ");
    expect(all).toContain("Recaps are for paid plans");
    expect(all).toContain("people who said yes to being recorded");
    expect(all).toContain("Anything said out loud at the table can still be in it");
    expect(all).toContain("Try again writes a new one and throws away your changes");
    expect(all).toContain("never the recap itself");
    expect(all).toContain("Nothing goes to your players until you press Post to the table");
    expect(all).toContain("if a player has stopped being recorded since it was written");
    expect(all).toContain("or it names something you keep secret");
    expect(all).toContain("checks the sentences against what was said");
    expect(all).toContain("If too little is left, there is no recap");
    expect(all).toContain("It also takes out compass directions");
    // Says no more than the check does: not "never wrong", and no technical words (#2072).
    expect(all).not.toMatch(/never wrong|always right|wrong one|every detail|beats?\b|fact check|AI model/i);
    expect(all).not.toMatch(/narrat|music/i); // not built yet (#1383)
    expect(recapEmailStep).toContain("once for each recap");
  });
});

describe("the switches and What's new agree", () => {
  it("holds 'Recaps for your table' back exactly while recaps are not live", () => {
    const title = "Recaps for your table";
    expect(held.some((h) => h.title === title)).toBe(!RECAPS_LIVE);
    expect(entries.some((e) => e.title === title)).toBe(RECAPS_LIVE);
  });

  // Each of these moves up on the same day as its switch, and not before: a test per pair, so
  // turning a switch on without its line, or the line up without its switch, fails (#1463).
  it.each([
    ["Choose what recaps do", RECAPS_LIVE],
    ["Recaps fit your session", RECAPS_LIVE],
    ["Recaps stick to what happened", RECAPS_LIVE],
    ["New names from your recap", RECAP_NAMES_LIVE],
    ["Share a recap by a link", RECAP_SHARE_LIVE],
    ["Approve the text before any audio", RECAP_AUDIO_LIVE],
    ["Narrated recaps with music", RECAP_AUDIO_LIVE],
    ["Play a recap's voice and music together", RECAP_AUDIO_LIVE],
  ] as const)("holds %s back exactly while its switch is off", (title, on) => {
    expect(held.some((h) => h.title === title)).toBe(!on);
    expect(entries.some((e) => e.title === title)).toBe(on);
  });

  it("has what #1457 and #1462 ship (the DM-screen buttons) in the recaps entry", () => {
    const text = [...entries, ...held].find((e) => e.title === "Recaps for your table")?.what ?? "";
    expect(text).toContain("on the DM screen or the website");
  });

  it("never has audio live without recaps", () => {
    expect(!RECAP_AUDIO_LIVE || RECAPS_LIVE).toBe(true);
  });

  it("never has the names to check live without recaps, and says them only while live (#2031)", () => {
    expect(!RECAP_NAMES_LIVE || RECAPS_LIVE).toBe(true);
    const step = recapSteps.find((s) => s.names?.some((n) => n.name === "📝 Check new names"));
    expect(step !== undefined).toBe(RECAP_NAMES_LIVE);
  });

  // Read directly, so the wording is checked while the switch is still off.
  it("words the names step both ways, and names the narration only when it is on (#2031)", () => {
    const off = recapNamesStep(false);
    const on = recapNamesStep(true);
    expect(off.text).not.toMatch(/narrat/i);
    expect(on.text).toContain("the narration never reads it");
    for (const step of [off, on]) {
      expect(step.text).toContain("📝 Check new names");
      expect(step.text).toContain("Nothing is added to your game until you say so");
      expect(step.text).toContain("never posted to the table");
      expect(step.names).toEqual([{ name: "📝 Check new names", file: "ui/names.py" }]);
    }
  });
});
