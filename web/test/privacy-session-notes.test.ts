// The privacy page's two session-notes sentences (#3027). With the switch off (the real state until the doors are
// deployed) the page reads exactly as it did before. The switch-on wording is checked on the privacy-lines module with
// the switch mocked: the page itself is rendered by Astro, which does not take the mock.
import { getContainerRenderer } from "@astrojs/preact/container-renderer";
import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { loadRenderers } from "astro:container";
import type { AstroComponentFactory } from "astro/runtime/server/index.js";
import { beforeAll, describe, expect, it, vi } from "vitest";

const live = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/session-notes-switch", () => ({
  get SESSION_NOTES_LIVE() {
    return live.on;
  },
}));

const found = import.meta.glob<{ default: AstroComponentFactory }>("../src/pages/legal/privacy.astro", { eager: true });
let container: AstroContainer;

beforeAll(async () => {
  container = await AstroContainer.create({ renderers: await loadRenderers([getContainerRenderer()]) });
});

const OLD_MAKES = "That means a recap, story facts, notes for the game master, and anything else DMbot writes from what was said.";
const OLD_STAYS = "Recaps (short summaries of a game) stay.";
const NEW_MAKES = "That means a recap, session notes (a numbered list of what happened in each game), story facts, notes for the game master, and anything else DMbot writes from what was said.";
const NEW_STAYS = "Recaps (short summaries of a game) and session notes (a numbered list of what happened in each game) stay.";

async function privacyText(): Promise<string> {
  const html = await container.renderToString(found["../src/pages/legal/privacy.astro"]!.default, {
    request: new Request("https://dmbot.example/legal/privacy"),
  });
  return html
    .replace(/<!--[^]*?-->/g, "")
    .replace(/<[^>]+>/g, " ")
    .replace(/&#x27;/g, "'")
    .replace(/&amp;/g, "&")
    .replace(/\s+/g, " ");
}

describe("the privacy page with session notes off", () => {
  it("reads exactly as before, with the old sentences and no mention of session notes", async () => {
    live.on = false;
    const text = await privacyText();
    expect(text).toContain(OLD_MAKES);
    expect(text).toContain(OLD_STAYS);
    expect(text).not.toContain("session notes");
  });
});

describe("the privacy lines for session notes", () => {
  it("with the switch on they name session notes, and the switch is read from the same place as the page", async () => {
    vi.resetModules();
    live.on = true;
    const on = (await import("../src/content/privacy-channels")).sessionNotesPrivacy;
    expect(on.live).toBe(true);
    expect(on.makes).toBe(NEW_MAKES);
    expect(on.stays).toBe(NEW_STAYS);

    vi.resetModules();
    live.on = false;
    const off = (await import("../src/content/privacy-channels")).sessionNotesPrivacy;
    expect(off.live).toBe(false);
  });
});
