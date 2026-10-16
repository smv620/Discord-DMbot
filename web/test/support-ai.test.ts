// The words about DMbot's helper answering questions sent to the team (#1432). Off today: the
// privacy page and the Support form say what is true while no helper runs; when it is switched on
// they say what the helper does. Every claim rests on CloudDev's #1431.
import { getContainerRenderer } from "@astrojs/preact/container-renderer";
import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { loadRenderers } from "astro:container";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { parsePage } from "./dom";

import { text } from "../src/content/account";
import { aiTrainingSentence } from "../src/content/legal";
import * as real from "../src/content/support-words";
import Privacy from "../src/pages/legal/privacy.astro";

// A switch the tests can flip; the real constant is false.
const live = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/support-words", async (original) => {
  const actual = await original<typeof import("../src/content/support-words")>();
  return {
    ...actual,
    get SUPPORT_AI_LIVE() {
      return live.on;
    },
  };
});

afterEach(() => {
  live.on = false;
});

const norm = (s: string): string => s.replace(/\s+/g, " ").trim();

let container: AstroContainer;
beforeAll(async () => {
  container = await AstroContainer.create({
    renderers: await loadRenderers([getContainerRenderer()]),
  });
});

async function privacy(): Promise<string> {
  const html = await container.renderToString(Privacy, {
    request: new Request("https://dmbot.example/legal/privacy"),
  });
  return norm(parsePage(html).querySelector("main")?.textContent ?? "");
}

describe("while the helper is off (today)", () => {
  it("is switched off, and the Support form says what is true", () => {
    expect(real.SUPPORT_AI_LIVE).toBe(false);
    expect(text.supportPrivacy).toBe(real.supportNote(false));
    expect(text.supportPrivacy).not.toMatch(/helper|AI|info@/);
  });

  it("the privacy page does not mention a helper, an acknowledgement or info@", async () => {
    const page = await privacy();
    expect(page).toContain("From your account page, you can write to the DMbot team. That message goes only to the team, privately");
    expect(page).not.toMatch(/helper|info@getdmbot\.com|writes the first answer/i);
  });
});

describe("while the helper is on", () => {
  it("the privacy page says who answers first, what is sent and to whom, who takes over and when, and how long", async () => {
    live.on = true;
    const page = await privacy();
    expect(page).toContain("you get an email from info@getdmbot.com to say we got it. That email does not repeat your words");
    expect(page).toContain("DMbot's helper, an AI, answers first. It is sent your subject, your message and the name of your plan, and it goes to Anthropic, the AI company named below");
    expect(page).toContain(aiTrainingSentence);
    expect(page).toContain("if the helper can't help, if you say you are unhappy, if you ask for a person, or after the helper has answered 5 times");
    expect(page).toContain("The team reads the question and the answers then");
    expect(page).toContain("your reply is added to the same question");
    expect(page).toContain("We keep the question and the answers for 12 months, then delete them");
    expect(page).toContain("we delete your messages and the answers too");
    expect(page).toContain("For that it is sent the subject, the message and the plan's name.");
  });

  it("the Support form's note names the company, what is sent, the training answer, who reads it and the sender", () => {
    const note = real.supportNote(true);
    expect(note).toContain("The helper is an AI from Anthropic");
    expect(note).toContain("It is sent your subject, your message and the name of your plan");
    expect(note).toContain(aiTrainingSentence);
    expect(note).toContain("a person on the team takes over and reads it");
    expect(note).toContain("We email you from info@getdmbot.com to say we got it");
    expect(note).toContain("We keep your message and the answers for 12 months");
    expect(note).not.toMatch(/privately/);
  });

  it("uses plain words", () => {
    const words = real.supportNote(true) + real.privacyParagraph() + real.anthropicAddition;
    expect(words).not.toMatch(/LLM|model|prompt|token|API|escalat|ticket|sentiment/i);
  });
});
