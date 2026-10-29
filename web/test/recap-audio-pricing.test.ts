// The narrated-recaps add-on on the pricing page (#1390): nothing while the audio is off; the
// row, who includes it and no price until the owner sets one when it is on.
import { getContainerRenderer } from "@astrojs/preact/container-renderer";
import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { loadRenderers } from "astro:container";
import { afterEach, describe, expect, it, vi } from "vitest";

import { parsePage } from "./dom";

import Pricing from "../src/pages/pricing.astro";

const live = vi.hoisted(() => ({ audio: false }));
vi.mock("../src/content/recap-words", async (original) => {
  const real = await original<typeof import("../src/content/recap-words")>();
  return {
    ...real,
    get RECAP_AUDIO_LIVE() {
      return live.audio;
    },
  };
});

afterEach(() => {
  live.audio = false;
});

describe("the pricing page", () => {
  async function page(): Promise<Document> {
    const container = await AstroContainer.create({
      renderers: await loadRenderers([getContainerRenderer()]),
    });
    return parsePage(
      await container.renderToString(Pricing, {
        request: new Request("https://dmbot.example/pricing"),
      }),
    );
  }

  it("has no add-on while the audio is off", async () => {
    const doc = await page();
    expect(doc.querySelector('[data-plan="recap-audio"]')).toBeNull();
    expect(doc.body.textContent).not.toMatch(/Narrated recaps/);
  });

  it("names the add-on, that Guild and Pro include it, and shows no price until one is set", async () => {
    live.audio = true;
    const doc = await page();
    const row = doc.querySelector('[data-plan="recap-audio"]');
    const words = row?.textContent ?? "";
    expect(words).toContain("Narrated recaps with music");
    expect(words).toContain("Comes with Guild and Pro.");
    expect(doc.body.textContent).toContain(
      "Extras are not part of any plan. Guild and Pro include this one.",
    );
    expect(words).not.toMatch(/\$/);
    expect(words).not.toMatch(/stem|render/i);
  });
});

describe("the plans question", () => {
  it("says nothing of narrated recaps while the audio is off", async () => {
    const { questions } = await import("../src/content/pricing");
    const answer = questions.find(
      (q) => q.question === "What's different between the plans?",
    )?.answer;
    expect(answer).not.toMatch(/Narrated/);
  });
});
