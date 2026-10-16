// The privacy page's section on posts in the public Q&A for DMbot (#1356). It must appear only
// when QAbot is live, and every sentence in it must say what the code does (the pull request lists
// the lines), with the one training sentence the Anthropic line already uses.
import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { beforeAll, describe, expect, it } from "vitest";

import { parsePage } from "./dom";

import QabotPrivacy from "../src/components/QabotPrivacy.astro";
import { QA_URL } from "../src/content/account";
import { SUPPORT_EMAIL } from "../src/content/help";
import { aiTrainingSentence } from "../src/content/legal";
import { QABOT_LIVE } from "../src/content/qabot";
import { entries, held } from "../src/content/whats-new";
import Privacy from "../src/pages/legal/privacy.astro";

const text = (node: Element | Document | null): string =>
  (node?.textContent ?? "").replace(/\s+/g, " ").trim();

let off: Document;
let on: Document;
let privacy: Document;
beforeAll(async () => {
  const container = await AstroContainer.create();
  const render = async (live: boolean): Promise<Document> =>
    parsePage(await container.renderToString(QabotPrivacy, { props: { live } }));
  off = await render(false);
  on = await render(true);
  privacy = parsePage(
    await container.renderToString(Privacy, { request: new Request("https://dmbot.example/legal/privacy") }),
  );
});

describe("the QAbot privacy section (#1356)", () => {
  it("shows nothing until QAbot is live", () => {
    expect(text(off.body)).toBe("");
    expect(off.querySelector("h2")).toBeNull();
  });

  it("is switched off today, so the privacy page does not mention QAbot yet", () => {
    // Flip QABOT_LIVE (content/qabot.ts) only when QAbot is running; see the note there.
    expect(QABOT_LIVE).toBe(false);
    expect(text(privacy.querySelector("main"))).not.toMatch(/QAbot/i);
    expect(privacy.querySelector("#public-questions")).toBeNull();
  });

  it("is on the privacy page exactly when the switch is on", () => {
    expect(privacy.querySelector("#public-questions") !== null).toBe(QABOT_LIVE);
  });

  it("sits where it ends a section: the next heading after it is 'How long we keep it'", () => {
    // Placed before that heading so the paragraphs above (no selling, locked keys) are not read as
    // part of the Q&A section when it is switched on.
    expect(text(privacy.querySelector("main"))).toContain("How long we keep it");
    const headings = [...privacy.querySelectorAll("main h2")].map((h) => h.textContent);
    const at = headings.indexOf("If you post in the public Q&A");
    if (QABOT_LIVE) expect(headings[at + 1]).toBe("How long we keep it");
    else expect(at).toBe(-1);
  });

  it("says posts are public on GitHub, who reads them, and what happens when it can't answer", () => {
    const words = text(on.body);
    expect(on.querySelector("h2")?.textContent).toBe("If you post in the public Q&A");
    expect(words).toContain("is on GitHub, where anyone can read it");
    expect(words).toContain("GitHub's own privacy terms apply there");
    expect(words).toContain("QAbot, a bot and not a person, sends the text of the post, and the replies under it, to an AI company (Anthropic) so its AI can write an answer");
    expect(words).toContain("it adds your question to the team's public list on GitHub (an issue), with a link to your post");
    expect(words).toContain("Nothing new is shared, because your post was already public");
  });

  it("uses the same training sentence as the Anthropic line, word for word", () => {
    expect(aiTrainingSentence).toBe("That text isn't used to train Anthropic's AI.");
    expect(text(on.body)).toContain(aiTrainingSentence);
    expect(text(privacy.querySelector("main"))).toContain(`It gets the text it needs, not sound. ${aiTrainingSentence}`);
    // Said once in the section, not twice.
    expect(text(on.body).match(/train/gi)).toHaveLength(1);
    expect(text(on.body)).toContain(`${aiTrainingSentence} It may also search the web, using words from your post.`);
  });

  it("links the Q&A and the one support address, and tells people to keep private things out", () => {
    const hrefs = [...on.querySelectorAll("a")].map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual([QA_URL, `mailto:${SUPPORT_EMAIL}`]);
    expect(text(on.body)).toContain("Don't post account, payment or personal details there, because anyone can read them");
    expect(text(on.body)).toContain("If you pay for DMbot, use \"Write to the DMbot team\" on your account page. If you don't, email");
  });

  it("uses no technical terms and makes no retention promise of its own", () => {
    const words = text(on.body);
    expect(words).not.toMatch(/knowledge graph|ontology|claims|extractor|database|API\b|LLM|workflow|token/i);
    expect(words).not.toMatch(/months|delete|deleted|encrypt|kept for|we keep/i);
  });
});

describe("the switch and What's new agree", () => {
  it("holds 'Ask a question in public' back exactly while QAbot is not live", () => {
    const title = "Ask a question in public";
    expect(held.some((h) => h.title === title)).toBe(!QABOT_LIVE);
    expect(entries.some((e) => e.title === title)).toBe(QABOT_LIVE);
  });
});
