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
  RECAPS_LIVE,
  emailsIntroTail,
  recapEmailStep,
  recapSteps,
} from "../src/content/recap-words";
import { entries, held } from "../src/content/whats-new";
import Help from "../src/pages/help.astro";
import Privacy from "../src/pages/legal/privacy.astro";

const text = (node: Element | Document | null): string =>
  (node?.textContent ?? "").replace(/\s+/g, " ").trim();

let off: Document;
let on: Document;
let onAudio: Document;
let help: Document;
let privacy: Document;
beforeAll(async () => {
  const container = await AstroContainer.create();
  const render = async (live: boolean, audio: boolean): Promise<Document> =>
    parsePage(await container.renderToString(RecapsPrivacy, { props: { live, audio } }));
  off = await render(false, false);
  on = await render(true, false);
  onAudio = await render(true, true);
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
  it("is switched off today and the page then has no word of it", () => {
    expect(RECAPS_LIVE).toBe(false);
    expect(RECAP_AUDIO_LIVE).toBe(false);
    expect(text(off.body)).toBe("");
    expect(privacy.querySelector("#recaps")).toBeNull();
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
  it("show nothing about recaps while recaps are off", () => {
    expect(help.querySelector("#recaps-heading")).toBeNull();
    expect(emailSteps.map((s) => s.text).join(" ")).not.toMatch(/recap/i);
    expect(emailsIntroTail).toBe("The only others are two Try It emails, each sent once.");
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

  it("never has audio live without recaps", () => {
    expect(!RECAP_AUDIO_LIVE || RECAPS_LIVE).toBe(true);
  });
});
